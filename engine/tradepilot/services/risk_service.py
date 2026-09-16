from datetime import datetime

from tradepilot.core.events import TOPIC_RISK, EventBus
from tradepilot.domain.risk import RiskLimit, RiskState
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.services.audit_service import AuditService


class RiskService:
    """Kill switch global + límites por cuenta + cierre de emergencia. El replicador consulta
    `allows()` antes de enviar cualquier orden."""

    def __init__(self, store: SQLiteStore, bus: EventBus, audit: AuditService, bridge=None, accounts=None) -> None:
        self.store = store
        self.bus = bus
        self.audit = audit
        self.bridge = bridge
        self.accounts = accounts
        self.rules_provider = lambda: []
        self.replication = None               # lo inyecta el contenedor
        self.master_flatten_grace = 20.0      # segundos sin copiar fills de la maestra tras cerrarla
        bus.subscribe("risk.naked", self._on_naked)
        self.limits: dict[str, RiskLimit] = {l.account_id: l for l in store.get_risk_limits()}
        self.kill_switch = store.get_kv("kill_switch", "0") == "1"
        self.kill_switch_reason = store.get_kv("kill_switch_reason")
        ts = store.get_kv("kill_switch_at")
        self.kill_switch_at = datetime.fromisoformat(ts) if ts else None

    def state(self) -> RiskState:
        return RiskState(kill_switch=self.kill_switch, kill_switch_reason=self.kill_switch_reason,
                         kill_switch_at=self.kill_switch_at, limits=list(self.limits.values()))

    async def kill(self, active: bool, reason: str | None = None, flatten: bool = False,
                   flatten_master: bool = True) -> RiskState:
        state = self.set_kill_switch(active, reason)
        if active and flatten:
            await self.flatten_all(include_master=flatten_master, reason="kill switch")
        return state

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

    # ---- cierre de emergencia ----
    async def flatten(self, account_id: str, reason: str = "") -> str:
        if self.bridge is None:
            raise RuntimeError("sin puente")
        master = self.bridge.health.master_account
        if self.replication is not None and master and account_id.lower() == master.lower():
            import time
            self.replication.suppress_master_until = time.monotonic() + self.master_flatten_grace
        self.audit.log("FLATTEN", f"Cierre de emergencia en {account_id}" + (f": {reason}" if reason else ""), target=account_id)
        reply = await self.bridge.flatten(account_id)
        if self.accounts:
            await self.accounts.sync_once()
        return reply

    def follower_accounts(self) -> list[str]:
        """Seguidoras de la maestra actual; si la maestra aún no se conoce, las de todas las reglas activas."""
        master = self.bridge.health.master_account if self.bridge else None
        seen: list[str] = []
        for r in self.rules_provider():
            if r.enabled and (master is None or r.master_matches(master)) and r.follower_account not in seen:
                seen.append(r.follower_account)
        return seen

    async def flatten_all(self, include_master: bool = False, reason: str = "") -> dict[str, str]:
        targets = self.follower_accounts()
        master = self.bridge.health.master_account if self.bridge else None
        if master is None:
            masters = {r.master_account for r in self.rules_provider() if r.enabled}
            master = next(iter(masters)) if len(masters) == 1 else None
        if include_master and master and master not in targets:
            targets.append(master)
        results: dict[str, str] = {}
        for acc in targets:
            try:
                results[acc] = await self.flatten(acc, reason)
            except Exception as exc:
                results[acc] = f"ERROR: {exc}"
                self.audit.log("ERROR", f"No se pudo cerrar {acc}: {exc}", target=acc)
        return results

    async def _on_naked(self, data: dict) -> None:
        """Stop rechazado en una seguidora: cerrar antes de que quede sin protección."""
        account = str(data.get("account", ""))
        pos = sum(abs(p.quantity) for p in (self.accounts.accounts.get(account).open_positions
                                             if self.accounts and account in self.accounts.accounts else []))
        self.audit.log("NAKED_CLOSE", f"{account}: {data.get('reason')} -> cerrando posición ({pos} contratos) por seguridad",
                       target=account)
        try:
            await self.flatten(account, "stop rechazado")
        except Exception as exc:
            self.audit.log("ERROR", f"No se pudo cerrar {account} tras stop rechazado: {exc}. ¡Revisa la cuenta a mano!",
                           target=account)

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
