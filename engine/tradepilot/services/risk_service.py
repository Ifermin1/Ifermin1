from datetime import datetime

from tradepilot.core.events import TOPIC_RISK, EventBus
from tradepilot.domain.risk import RiskLimit, RiskState
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.services.audit_service import AuditService


class RiskService:
    """Kill switch global + límites por cuenta. El replicador consulta
    `allows()` antes de enviar cualquier orden."""

    def __init__(self, store: SQLiteStore, bus: EventBus, audit: AuditService) -> None:
        self.store = store
        self.bus = bus
        self.audit = audit
        self.limits: dict[str, RiskLimit] = {l.account_id: l for l in store.get_risk_limits()}
        self.kill_switch = store.get_kv("kill_switch", "0") == "1"
        self.kill_switch_reason = store.get_kv("kill_switch_reason")
        ts = store.get_kv("kill_switch_at")
        self.kill_switch_at = datetime.fromisoformat(ts) if ts else None

    def state(self) -> RiskState:
        return RiskState(kill_switch=self.kill_switch, kill_switch_reason=self.kill_switch_reason,
                         kill_switch_at=self.kill_switch_at, limits=list(self.limits.values()))

    def set_kill_switch(self, active: bool, reason: str | None = None) -> RiskState:
        self.kill_switch = active
        self.kill_switch_reason = reason if active else None
        self.kill_switch_at = datetime.now() if active else None
        self.store.set_kv("kill_switch", "1" if active else "0")
        self.store.set_kv("kill_switch_reason", self.kill_switch_reason or "")
        self.store.set_kv("kill_switch_at", self.kill_switch_at.isoformat() if self.kill_switch_at else "")
        self.audit.log("KILL_SWITCH_ON" if active else "KILL_SWITCH_OFF",
                       f"Kill switch {'ACTIVADO' if active else 'desactivado'}" + (f": {reason}" if reason else ""))
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        return self.state()

    def upsert_limit(self, limit: RiskLimit) -> RiskLimit:
        self.limits[limit.account_id] = limit
        self.store.save_risk_limit(limit)
        self.audit.log("RISK_LIMIT_SET", f"Límites {limit.account_id}: pérdida diaria máx {limit.max_daily_loss}, "
                       f"tamaño máx {limit.max_position_size}, halted={limit.trading_halted}", target=limit.account_id)
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        return limit

    def allows(self, account_id: str, quantity: int) -> tuple[bool, str | None]:
        """¿Se puede enviar una orden de `quantity` a `account_id`?"""
        if self.kill_switch:
            return False, "kill switch global activo"
        limit = self.limits.get(account_id)
        if limit is None:
            return True, None
        if limit.trading_halted:
            return False, f"cuenta {account_id} en pausa por riesgo"
        if limit.max_position_size and quantity > limit.max_position_size:
            return False, f"qty {quantity} supera el máximo {limit.max_position_size} de {account_id}"
        return True, None
