from typing import List
from loguru import logger
from domain.replication import MasterEvent, ReplicationRule, ReplicationTask
from domain.audit import AuditEvent
from services.audit_service import AuditService
from infrastructure.brokers.ninja_zmq_bridge import NinjaZmqBridge
from infrastructure.persistence.sqlite_store import SQLiteStore
from bus.event_bus import bus

class ReplicationService:
    def __init__(self, bridge: NinjaZmqBridge, store: SQLiteStore, audit_service: AuditService):
        self.bridge = bridge
        self.store = store
        self.audit_service = audit_service
        self.rules: List[ReplicationRule] = self.store.get_all_rules()
        
    def add_rule(self, rule: ReplicationRule):
        self.rules.append(rule)
        self.store.save_rule(rule)
        self.audit_service.log_event(AuditEvent(
            event_type="RULE_ADDED",
            message=f"Nueva regla de replicación: {rule.master_account} -> {rule.follower_account} (x{rule.multiplier})"
        ))

    def update_rule_status(self, rule_id: str, enabled: bool):
        for r in self.rules:
            if r.id == rule_id:
                r.enabled = enabled
                self.store.save_rule(r)
                break

    def delete_rule(self, rule_id: str):
        self.rules = [r for r in self.rules if r.id != rule_id]
        self.store.delete_rule(rule_id)
        self.audit_service.log_event(AuditEvent(
            event_type="RULE_DELETED",
            message=f"Regla de replicación eliminada: {rule_id}"
        ))

    async def start(self):
        bus.subscribe("ninja.master.event", self._process_master_event)

    async def _process_master_event(self, data: dict):
        try:
            event = MasterEvent(**data)
        except Exception as e:
            logger.error(f"Error parseando evento maestro: {e}")
            return

        if event.msg_type not in ["EXECUTION", "ORDER_PENDING", "ORDER_MODIFIED", "ORDER_CANCELLED"]:
            return

        self.audit_service.log_event(AuditEvent(
            event_type="MASTER_RECEIVED",
            source_account=event.account,
            message=f"[{event.msg_type}] {event.action} {event.quantity} {event.symbol} @ {event.price}"
        ))

        for rule in self.rules:
            if not rule.enabled:
                continue
            if rule.master_account != event.account:
                continue
            if rule.symbol_filter and rule.symbol_filter != event.symbol:
                continue

            scaled_qty = int(event.quantity * rule.multiplier)
            if scaled_qty <= 0:
                self.audit_service.log_event(AuditEvent(
                    event_type="SKIPPED",
                    source_account=event.account,
                    target_account=rule.follower_account,
                    message=f"Regla {rule.id} ignorada. Quantity final = {scaled_qty} <= 0"
                ))
                continue

            self.audit_service.log_event(AuditEvent(
                event_type="RULE_MATCHED",
                source_account=event.account,
                target_account=rule.follower_account,
                message=f"Regla aplicada: Qty {event.quantity} x {rule.multiplier} = {scaled_qty}"
            ))

            task = ReplicationTask(
                rule_id=rule.id,
                master_event=event,
                target_account=rule.follower_account,
                scaled_quantity=scaled_qty,
                master_order_id=event.order_id
            )

            await self._run_replication_task(task)

    async def _run_replication_task(self, task: ReplicationTask):
        event = task.master_event
        try:
            await self.bridge.send_order(
                target_account=task.target_account,
                action=event.action,
                symbol=event.symbol,
                quantity=task.scaled_quantity,
                order_type=event.order_type,
                master_order_id=task.master_order_id,
                msg_type=event.msg_type,
                price=event.price
            )
            self.audit_service.log_event(AuditEvent(
                event_type="PAYLOAD_PUBLISHED",
                source_account=event.account,
                target_account=task.target_account,
                message=f"Replicado -> action: {event.action}, qty: {task.scaled_quantity}, sym: {event.symbol}"
            ))
        except Exception as e:
            self.audit_service.log_event(AuditEvent(
                event_type="ERROR",
                source_account=event.account,
                target_account=task.target_account,
                message=f"Fallo replicación a {task.target_account}: {e}"
            ))
