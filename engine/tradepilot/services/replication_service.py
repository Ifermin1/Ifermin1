import uuid

from loguru import logger

from tradepilot.core.enums import REPLICABLE_MSG_TYPES
from tradepilot.core.events import TOPIC_MASTER_EVENT, EventBus
from tradepilot.domain.replication import MasterEvent, ReplicationRule, ReplicationTask
from tradepilot.infrastructure.brokers.base import BrokerBridge
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.services.audit_service import AuditService
from tradepilot.services.risk_service import RiskService


class ReplicationService:
    def __init__(self, bridge: BrokerBridge, store: SQLiteStore, audit: AuditService,
                 risk: RiskService, bus: EventBus) -> None:
        self.bridge = bridge
        self.store = store
        self.audit = audit
        self.risk = risk
        self.bus = bus
        self.rules: list[ReplicationRule] = store.get_all_rules()
        self.stats = {"events_in": 0, "orders_out": 0, "blocked": 0, "errors": 0}

    async def start(self) -> None:
        self.bus.subscribe(TOPIC_MASTER_EVENT, self.process_master_event)

    # ---- CRUD de reglas ----
    def add_rule(self, master: str, follower: str, multiplier: float = 1.0, symbol_filter: str | None = None,
                 enabled: bool = True) -> ReplicationRule:
        rule = ReplicationRule(id=str(uuid.uuid4()), master_account=master.strip(), follower_account=follower.strip(),
                               multiplier=multiplier, symbol_filter=symbol_filter, enabled=enabled)
        self.rules.append(rule)
        self.store.save_rule(rule)
        self.audit.log("RULE_ADDED", f"Nueva regla: {rule.master_account} -> {rule.follower_account} (x{rule.multiplier})",
                       source=rule.master_account, target=rule.follower_account)
        return rule

    def update_rule(self, rule_id: str, **changes) -> ReplicationRule | None:
        for i, r in enumerate(self.rules):
            if r.id == rule_id:
                updated = r.model_copy(update={k: v for k, v in changes.items() if v is not None})
                updated = ReplicationRule.model_validate(updated.model_dump())
                self.rules[i] = updated
                self.store.save_rule(updated)
                self.audit.log("RULE_UPDATED", f"Regla {rule_id[:8]} actualizada: {changes}",
                               source=updated.master_account, target=updated.follower_account)
                return updated
        return None

    def delete_rule(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.id != rule_id]
        if len(self.rules) == before:
            return False
        self.store.delete_rule(rule_id)
        self.audit.log("RULE_DELETED", f"Regla {rule_id[:8]} eliminada")
        return True

    # ---- procesamiento ----
    async def process_master_event(self, data: dict) -> list[ReplicationTask]:
        try:
            event = MasterEvent(**data)
        except Exception as exc:
            self.stats["errors"] += 1
            logger.error(f"Evento maestro inválido: {exc} | {data}")
            return []
        if event.msg_type not in REPLICABLE_MSG_TYPES:
            return []

        self.stats["events_in"] += 1
        self.audit.log("MASTER_RECEIVED", f"[{event.msg_type}] {event.action} {event.quantity} {event.symbol} @ {event.price}",
                       source=event.account, details={"order_id": event.order_id})

        tasks: list[ReplicationTask] = []
        for rule in self.rules:
            if not rule.matches(event):
                continue
            qty = rule.scale(event.quantity)
            if qty <= 0:
                self.audit.log("SKIPPED", f"Regla {rule.id[:8]}: qty escalada {qty} <= 0",
                               source=event.account, target=rule.follower_account)
                continue
            ok, reason = self.risk.allows(rule.follower_account, qty)
            if not ok:
                self.stats["blocked"] += 1
                self.audit.log("BLOCKED", f"Bloqueado por riesgo: {reason}", source=event.account,
                               target=rule.follower_account)
                continue
            task = ReplicationTask(rule_id=rule.id, master_event=event, target_account=rule.follower_account,
                                   scaled_quantity=qty, master_order_id=event.order_id)
            await self._execute(task)
            tasks.append(task)
        return tasks

    async def _execute(self, task: ReplicationTask) -> None:
        ev = task.master_event
        try:
            await self.bridge.send_order(target_account=task.target_account, action=ev.action, symbol=ev.symbol,
                                         quantity=task.scaled_quantity, order_type=ev.order_type,
                                         master_order_id=task.master_order_id, msg_type=ev.msg_type, price=ev.price)
            task.status = "SENT"
            self.stats["orders_out"] += 1
            self.audit.log("REPLICATED", f"{ev.action} {task.scaled_quantity} {ev.symbol} -> {task.target_account}",
                           source=ev.account, target=task.target_account,
                           details={"rule_id": task.rule_id, "master_order_id": task.master_order_id})
        except Exception as exc:
            task.status = "ERROR"
            self.stats["errors"] += 1
            self.audit.log("ERROR", f"Fallo replicando a {task.target_account}: {exc}",
                           source=ev.account, target=task.target_account)
