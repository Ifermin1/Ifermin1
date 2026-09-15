import uuid
from collections import OrderedDict
from datetime import datetime

from loguru import logger

from tradepilot.core.enums import (MSG_HEARTBEAT, MSG_ORDER_STATUS, MSG_POSITION, MSG_PRICE,
                                   REPLICABLE_MSG_TYPES)
from tradepilot.core.events import TOPIC_MASTER_EVENT, TOPIC_PRICE, EventBus
from tradepilot.domain.replication import MasterEvent, ReplicationRule, ReplicationTask
from tradepilot.infrastructure.brokers.base import BrokerBridge
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.services.account_service import AccountService
from tradepilot.services.audit_service import AuditService
from tradepilot.services.risk_service import RiskService

REJECTED_STATES = {"REJECTED", "ERROR"}


class ReplicationService:
    """Recibe todo lo que publica el addon de NinjaTrader por 5555 y:
    - replica las órdenes del maestro a los followers según las reglas,
    - registra los ACK / fills / rechazos que devuelven los followers,
    - actualiza heartbeat, precios y posiciones sin contarlos como errores.
    """

    def __init__(self, bridge: BrokerBridge, store: SQLiteStore, audit: AuditService,
                 risk: RiskService, bus: EventBus, accounts: AccountService | None = None) -> None:
        self.bridge = bridge
        self.store = store
        self.audit = audit
        self.risk = risk
        self.bus = bus
        self.accounts = accounts
        self.rules: list[ReplicationRule] = store.get_all_rules()
        self.stats = {"events_in": 0, "orders_out": 0, "blocked": 0, "errors": 0, "rejected": 0, "fills": 0, "duplicates": 0}
        self._seen: OrderedDict[tuple, None] = OrderedDict()

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

    def find_link(self, master: str, follower: str) -> ReplicationRule | None:
        """Regla simple (sin filtro de símbolo) entre dos cuentas, si existe."""
        for r in self.rules:
            if r.master_matches(master) and r.follower_account.strip().lower() == follower.strip().lower() and not r.symbol_filter:
                return r
        return None

    def link(self, master: str, follower: str, multiplier: float = 1.0, enabled: bool = True) -> ReplicationRule:
        """Vincula follower al master de un clic: crea la regla o actualiza la existente."""
        if master.strip().lower() == follower.strip().lower():
            raise ValueError("una cuenta no puede copiarse a sí misma")
        existing = self.find_link(master, follower)
        if existing is None:
            return self.add_rule(master, follower, multiplier, None, enabled)
        return self.update_rule(existing.id, multiplier=multiplier, enabled=enabled)  # type: ignore[return-value]

    def unlink(self, master: str, follower: str) -> bool:
        existing = self.find_link(master, follower)
        return self.delete_rule(existing.id) if existing else False

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

    # ---- entrada de mensajes ----
    async def process_master_event(self, data: dict) -> list[ReplicationTask]:
        msg_type = str(data.get("msg_type", "")).upper()

        if msg_type == MSG_HEARTBEAT:
            self.bridge.health.last_heartbeat = datetime.now()
            if data.get("account"):
                self.bridge.health.master_account = str(data["account"])
            return []
        if msg_type == MSG_PRICE:
            await self.bus.publish(TOPIC_PRICE, data)
            return []
        if msg_type == MSG_POSITION:
            await self._on_position(data)
            return []
        if msg_type == MSG_ORDER_STATUS:
            self._on_follower_status(data)
            return []
        if msg_type not in REPLICABLE_MSG_TYPES:
            logger.debug(f"Mensaje ignorado del addon: {msg_type}")
            return []

        try:
            event = MasterEvent(**data)
        except Exception as exc:
            self.stats["errors"] += 1
            logger.error(f"Evento inválido: {exc} | {data}")
            return []

        if self._is_duplicate(event):
            self.stats["duplicates"] += 1
            return []

        if event.is_follower_ack:
            self._on_follower_fill(event)
            return []

        return await self._replicate(event)

    # ---- maestro ----
    async def _replicate(self, event: MasterEvent) -> list[ReplicationTask]:
        self.stats["events_in"] += 1
        self.audit.log("MASTER_RECEIVED", f"[{event.msg_type}] {event.action} {event.quantity} {event.symbol} @ {event.price}",
                       source=event.account, details={"order_id": event.order_id, "state": event.state})

        tasks: list[ReplicationTask] = []
        matched = 0
        for rule in self.rules:
            if not rule.matches(event):
                continue
            matched += 1
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
        if matched == 0:
            self._explain_no_match(event)
        return tasks

    def _explain_no_match(self, event: MasterEvent) -> None:
        """Deja en la auditoría por qué no se replicó, para no fallar en silencio."""
        enabled = [r for r in self.rules if r.enabled]
        if not enabled:
            reason = "no hay reglas activas"
        elif not any(r.master_matches(event.account) for r in enabled):
            masters = sorted({r.master_account for r in enabled})
            reason = f"ninguna regla tiene como maestro '{event.account}' (maestros configurados: {', '.join(masters)})"
        else:
            filters = sorted({r.symbol_filter for r in enabled if r.master_matches(event.account) and r.symbol_filter})
            reason = f"el símbolo '{event.symbol}' no pasa el filtro ({', '.join(filters)})"
        self.audit.log("NO_RULE", f"No replicado: {reason}", source=event.account)

    async def _execute(self, task: ReplicationTask) -> None:
        ev = task.master_event
        try:
            await self.bridge.send_order(target_account=task.target_account, action=ev.action, symbol=ev.symbol,
                                         quantity=task.scaled_quantity, order_type=ev.order_type,
                                         master_order_id=task.master_order_id, msg_type=ev.msg_type, price=ev.price,
                                         limit_price=ev.limit_price, stop_price=ev.stop_price)
            task.status = "SENT"
            self.stats["orders_out"] += 1
            self.audit.log("REPLICATED", f"[{ev.msg_type}] {ev.action} {task.scaled_quantity} {ev.symbol} -> {task.target_account}",
                           source=ev.account, target=task.target_account,
                           details={"rule_id": task.rule_id, "master_order_id": task.master_order_id})
        except Exception as exc:
            task.status = "ERROR"
            self.stats["errors"] += 1
            self.audit.log("ERROR", f"Fallo replicando a {task.target_account}: {exc}",
                           source=ev.account, target=task.target_account)

    # ---- followers (ACK de vuelta) ----
    def _on_follower_fill(self, event: MasterEvent) -> None:
        self.stats["fills"] += 1
        self.audit.log("FOLLOWER_FILL", f"{event.account}: {event.action} {event.quantity} {event.symbol} @ {event.price}",
                       target=event.account, details={"master_order_id": event.master_order_id, "order_id": event.order_id})

    def _on_follower_status(self, data: dict) -> None:
        account = str(data.get("account", ""))
        state = str(data.get("state", "")).upper()
        error = str(data.get("error") or "")
        native = str(data.get("native_error") or "")
        desc = f"{data.get('action', '')} {data.get('quantity', '')} {data.get('symbol', '')} [{state}]"
        details = {"master_order_id": data.get("master_order_id"), "order_id": data.get("order_id"), "filled": data.get("filled")}
        if state in REJECTED_STATES or error:
            self.stats["rejected"] += 1
            self.audit.log("FOLLOWER_REJECTED", f"{account}: {desc} {error} {native}".strip(), target=account, details=details)
        else:
            self.audit.log("FOLLOWER_STATUS", f"{account}: {desc}", target=account, details=details)

    async def _on_position(self, data: dict) -> None:
        if self.accounts is None:
            return
        try:
            await self.accounts.update_position(str(data.get("account", "")), str(data.get("symbol", "")),
                                                str(data.get("market_position", "FLAT")), int(data.get("quantity") or 0),
                                                float(data.get("avg_price") or 0.0))
        except Exception as exc:
            logger.warning(f"POSITION inválida: {exc} | {data}")

    def _is_duplicate(self, event: MasterEvent) -> bool:
        key = event.dedup_key
        if key in self._seen:
            return True
        self._seen[key] = None
        if len(self._seen) > 2000:
            self._seen.popitem(last=False)
        return False
