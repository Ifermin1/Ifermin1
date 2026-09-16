import time
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

STOP_TYPES = {"STOPMARKET", "STOPLIMIT", "STOP", "MIT"}

REJECTED_STATES = {"REJECTED", "ERROR"}


class ReplicationService:
    """Recibe todo lo que publica el addon de NinjaTrader por 5555 y:
    - replica las órdenes del maestro a los followers según las reglas,
    - registra los ACK / fills / rechazos que devuelven los followers,
    - actualiza heartbeat, precios y posiciones sin contarlos como errores.
    """

    def __init__(self, bridge: BrokerBridge, store: SQLiteStore, audit: AuditService,
                 risk: RiskService, bus: EventBus, accounts: AccountService | None = None,
                 journal=None, close_on_stop_reject: bool = True) -> None:
        self.bridge = bridge
        self.store = store
        self.audit = audit
        self.risk = risk
        self.bus = bus
        self.accounts = accounts
        self.journal = journal
        self.close_on_stop_reject = close_on_stop_reject
        self.sync = None                      # SyncService, lo inyecta el contenedor
        self._last_seq: int | None = None
        self.suppress_master_until: float = 0.0   # tras un FLATTEN de la maestra, sus fills no se copian
        self.rules: list[ReplicationRule] = store.get_all_rules()
        self.stats = {"events_in": 0, "orders_out": 0, "blocked": 0, "errors": 0, "rejected": 0, "fills": 0, "duplicates": 0,
                      "latency_ms_last": None, "latency_ms_avg": None, "slippage_last": None, "slippage_avg": None,
                      "seq_gaps": 0, "addon_restarts": 0, "flattens": 0}
        self._seen: OrderedDict[tuple, None] = OrderedDict()
        # (follower, master_order_id) -> qty de la orden pendiente que ya copiamos (stop / take profit)
        self._sent_pending: OrderedDict[tuple, int] = OrderedDict()
        # (follower, master_order_id) -> qty que el follower ya ejecutó de esa orden
        self._follower_filled: OrderedDict[tuple, int] = OrderedDict()
        # master_order_id -> (precio, monotonic al recibirlo, action)  para medir latencia y deslizamiento
        self._master_execs: OrderedDict[str, tuple] = OrderedDict()

    async def start(self) -> None:
        self.bus.subscribe(TOPIC_MASTER_EVENT, self.process_master_event)

    # ---- CRUD de reglas ----
    def add_rule(self, master: str, follower: str, multiplier: float = 1.0, symbol_filter: str | None = None,
                 enabled: bool = True, **extra) -> ReplicationRule:
        rule = ReplicationRule(id=str(uuid.uuid4()), master_account=master.strip(), follower_account=follower.strip(),
                               multiplier=multiplier, symbol_filter=symbol_filter, enabled=enabled,
                               **{k: v for k, v in extra.items() if v is not None})
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

    def link(self, master: str, follower: str, multiplier: float = 1.0, enabled: bool = True, **extra) -> ReplicationRule:
        """Vincula follower al master de un clic: crea la regla o actualiza la existente."""
        if master.strip().lower() == follower.strip().lower():
            raise ValueError("una cuenta no puede copiarse a sí misma")
        existing = self.find_link(master, follower)
        if existing is None:
            return self.add_rule(master, follower, multiplier, None, enabled, **extra)
        return self.update_rule(existing.id, multiplier=multiplier, enabled=enabled, **extra)  # type: ignore[return-value]

    def unlink(self, master: str, follower: str) -> bool:
        existing = self.find_link(master, follower)
        return self.delete_rule(existing.id) if existing else False

    def update_rule(self, rule_id: str, **changes) -> ReplicationRule | None:
        for i, r in enumerate(self.rules):
            if r.id == rule_id:
                updated = r.model_copy(update={k: v for k, v in changes.items() if v is not None or k == "target_root"})
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
        if self.journal and msg_type != MSG_PRICE:
            self.journal.write("in", data)
        self._check_seq(data)

        if msg_type == "ENTRY_MISSED":
            self.audit.log("ENTRY_MISSED", f"{data.get('account')}: entrada límite {data.get('action')} {data.get('quantity')} "
                           f"{data.get('symbol')} cancelada sin llenarse; la seguidora NO tiene esa entrada",
                           target=str(data.get("account", "")), details={"master_order_id": data.get("master_order_id")})
            return []
        if msg_type == "FLATTENED":
            self.stats["flattens"] += 1
            self.audit.log("FLATTENED", f"{data.get('account')}: {data.get('orders_cancelled', 0)} órdenes canceladas, "
                           f"{data.get('instruments_closed', 0)} instrumentos cerrados", target=str(data.get("account", "")))
            return []

        if msg_type == MSG_HEARTBEAT:
            self.bridge.health.last_heartbeat = datetime.now()
            if data.get("account"):
                self.bridge.health.master_account = str(data["account"])
            if data.get("version"):
                self.bridge.note_addon_version(str(data["version"]))
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

        master = self.bridge.health.master_account
        if master and event.account.strip().lower() != master.strip().lower():
            # operación manual en una cuenta que no es la maestra (p. ej. cerrar a mano una seguidora)
            self.audit.log("ACCOUNT_FILL", f"{event.account}: {event.action} {event.quantity} {event.symbol} @ {event.price} (manual, no replicado)",
                           target=event.account, details={"order_id": event.order_id})
            return []

        return await self._replicate(event)

    # ---- maestro ----
    async def _replicate(self, event: MasterEvent) -> list[ReplicationTask]:
        self.stats["events_in"] += 1
        self.audit.log("MASTER_RECEIVED", f"[{event.msg_type}] {event.action} {event.quantity} {event.symbol} @ {event.price}",
                       source=event.account, details={"order_id": event.order_id, "state": event.state})

        if event.msg_type == "EXECUTION":
            self._remember(self._master_execs, event.order_id, (event.price, time.monotonic(), event.action))

        if time.monotonic() < self.suppress_master_until:
            # La maestra se está cerrando por emergencia: las seguidoras se cierran por su cuenta,
            # copiar este fill las dejaría con posición contraria.
            self.audit.log("SKIPPED", f"[{event.msg_type}] {event.action} {event.quantity} {event.symbol}: cierre de emergencia de la maestra, no se copia",
                           source=event.account)
            return []

        tasks: list[ReplicationTask] = []
        matched = 0
        for rule in self.rules:
            if not rule.matches(event):
                continue
            matched += 1
            qty = rule.scale(event.quantity)
            symbol = rule.map_symbol(event.symbol)
            key = (rule.follower_account.lower(), event.order_id)
            if event.msg_type == "EXECUTION" and key in self._sent_pending \
                    and self._follower_filled.get(key, 0) >= self._sent_pending[key]:
                # El follower ya ejecutó su propia copia de esa orden (stop / TP): copiar el fill sería una salida doble.
                self.audit.log("SKIPPED", f"Fill del maestro no copiado: {rule.follower_account} ya ejecutó su orden {event.order_id[:8]}",
                               source=event.account, target=rule.follower_account)
                continue
            if qty <= 0:
                self.audit.log("SKIPPED", f"Regla {rule.id[:8]}: qty escalada {qty} <= 0",
                               source=event.account, target=rule.follower_account)
                continue
            ok, reason = self.risk.allows(rule.follower_account, qty, symbol, event.action)
            if ok and self.accounts is not None and not self.accounts.is_enabled(rule.follower_account):
                ok, reason = False, f"cuenta {rule.follower_account} desactivada en la consola"
            if ok and self._desync_blocks(rule.follower_account, event):
                ok, reason = False, f"{rule.follower_account} desincronizada: solo se copian salidas hasta igualarla"
            if not ok:
                self.stats["blocked"] += 1
                self.audit.log("BLOCKED", f"Bloqueado por riesgo: {reason}", source=event.account,
                               target=rule.follower_account)
                continue
            task = ReplicationTask(rule_id=rule.id, master_event=event, target_account=rule.follower_account,
                                   scaled_quantity=qty, master_order_id=event.order_id)
            await self._execute(task, rule, symbol)
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

    def _entry_params(self, rule: ReplicationRule, ev: MasterEvent, symbol: str) -> dict | None:
        """Entrada a mercado del maestro: si la regla usa límite con tolerancia y la copia AUMENTA la
        exposición de la seguidora, se manda como límite. Las salidas van siempre a mercado."""
        if ev.msg_type != "EXECUTION" or rule.entry_mode != "limit" or self.accounts is None:
            return None
        pos = self.accounts.position(rule.follower_account, symbol)
        buying = ev.action.upper().startswith("BUY")
        reduces = (pos > 0 and not buying) or (pos < 0 and buying)
        if reduces:
            return None
        return {"entry_mode": "limit", "tolerance_ticks": rule.tolerance_ticks,
                "entry_timeout_s": rule.entry_timeout_s, "entry_fallback": rule.entry_fallback}

    async def _execute(self, task: ReplicationTask, rule: ReplicationRule | None = None, symbol: str | None = None) -> None:
        ev = task.master_event
        symbol = symbol or ev.symbol
        entry = self._entry_params(rule, ev, symbol) if rule else None
        try:
            await self.bridge.send_order(target_account=task.target_account, action=ev.action, symbol=symbol,
                                         quantity=task.scaled_quantity, order_type=ev.order_type,
                                         master_order_id=task.master_order_id, msg_type=ev.msg_type, price=ev.price,
                                         limit_price=ev.limit_price, stop_price=ev.stop_price, entry=entry)
            if self.journal:
                self.journal.write("out", {"msg_type": ev.msg_type, "account": task.target_account, "action": ev.action,
                                           "symbol": symbol, "quantity": task.scaled_quantity, "order_type": ev.order_type,
                                           "master_order_id": task.master_order_id, "rule_id": task.rule_id, **(entry or {})})
            task.status = "SENT"
            self.stats["orders_out"] += 1
            if ev.msg_type == "ORDER_PENDING":
                self._remember(self._sent_pending, (task.target_account.lower(), task.master_order_id), task.scaled_quantity)
            how = f" (límite ±{entry['tolerance_ticks']} ticks)" if entry else ""
            self.audit.log("REPLICATED", f"[{ev.msg_type}] {ev.action} {task.scaled_quantity} {symbol}{how} -> {task.target_account}",
                           source=ev.account, target=task.target_account,
                           details={"rule_id": task.rule_id, "master_order_id": task.master_order_id})
        except Exception as exc:
            task.status = "ERROR"
            self.stats["errors"] += 1
            self.audit.log("ERROR", f"Fallo replicando a {task.target_account}: {exc}",
                           source=ev.account, target=task.target_account)

    def _desync_blocks(self, follower: str, event: MasterEvent) -> bool:
        """Con DESYNC solo pasan las copias que reducen la exposición actual de la seguidora."""
        snap = self.accounts.accounts.get(follower) if self.accounts else None
        if not snap or not snap.desync:
            return False
        pos = self.accounts.position(follower, event.symbol)
        buying = event.action.upper().startswith("BUY")
        reduces = (pos > 0 and not buying) or (pos < 0 and buying)
        return not reduces

    def _check_seq(self, data: dict) -> None:
        seq = data.get("seq")
        if not isinstance(seq, int):
            return
        if self._last_seq is not None:
            if seq < self._last_seq:
                self.stats["addon_restarts"] += 1
                self.audit.log("ADDON_RESTART", f"El addon de NinjaTrader se reinició (seq {self._last_seq} -> {seq}); revisando posiciones")
            elif seq > self._last_seq + 1:
                missed = seq - self._last_seq - 1
                self.stats["seq_gaps"] += missed
                self.audit.log("GAP", f"Se perdieron {missed} mensajes del addon (seq {self._last_seq} -> {seq}); revisando posiciones",
                               details={"missed": missed})
        self._last_seq = seq

    # ---- followers (ACK de vuelta) ----
    def _on_follower_fill(self, event: MasterEvent) -> None:
        self.stats["fills"] += 1
        key = (event.account.lower(), event.master_order_id)
        self._remember(self._follower_filled, key, self._follower_filled.get(key, 0) + event.quantity)
        msg = f"{event.account}: {event.action} {event.quantity} {event.symbol} @ {event.price}"
        details: dict = {"master_order_id": event.master_order_id, "order_id": event.order_id}
        ref = self._master_execs.get(event.master_order_id)
        if ref:
            m_price, m_at, m_action = ref
            latency_ms = round((time.monotonic() - m_at) * 1000)
            # deslizamiento con signo: positivo = peor para el follower
            worse = event.price - m_price if m_action.upper().startswith("BUY") else m_price - event.price
            slip = round(worse, 4)
            details.update({"latency_ms": latency_ms, "master_price": m_price, "slippage": slip})
            msg += f" (maestro {m_price}, {'+' if slip >= 0 else ''}{slip}, {latency_ms} ms)"
            self.stats["latency_ms_last"], self.stats["slippage_last"] = latency_ms, slip
            self.stats["latency_ms_avg"] = round(_ema(self.stats["latency_ms_avg"], latency_ms))
            self.stats["slippage_avg"] = round(_ema(self.stats["slippage_avg"], slip), 4)
        self.audit.log("FOLLOWER_FILL", msg, target=event.account, details=details)

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
            order_type = str(data.get("order_type", "")).upper().replace("_", "")
            if order_type in STOP_TYPES and self.close_on_stop_reject:
                self.bus.publish_nowait("risk.naked", {"account": account, "reason": f"stop rechazado: {error} {native}".strip()})
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

    @staticmethod
    def _remember(store: OrderedDict, key, value, limit: int = 2000) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > limit:
            store.popitem(last=False)

    def _is_duplicate(self, event: MasterEvent) -> bool:
        key = event.dedup_key
        if key in self._seen:
            return True
        self._seen[key] = None
        if len(self._seen) > 2000:
            self._seen.popitem(last=False)
        return False


def _ema(prev: float | None, value: float, alpha: float = 0.3) -> float:
    return value if prev is None else prev + alpha * (value - prev)
