import json
from datetime import datetime, timedelta

from loguru import logger

from tradepilot.core.events import TOPIC_RISK, EventBus
from tradepilot.domain.risk import RiskLimit, RiskState, Schedule
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
        raw = store.get_kv("schedule")
        self.schedule = Schedule.model_validate(json.loads(raw)) if raw else Schedule()
        self.session_closed_day: str = store.get_kv("session_closed_day", "") or ""
        self.heartbeat_timeout = 30.0
        self.addon_silent = False
        self._warned_80: set[tuple[str, str]] = set()   # (cuenta, día) avisadas al 80 %
        self.now = datetime.now                       # inyectable en pruebas
        bus.subscribe("risk.naked", self._on_naked)
        self.limits: dict[str, RiskLimit] = {l.account_id: l for l in store.get_risk_limits()}
        self.kill_switch = store.get_kv("kill_switch", "0") == "1"
        self.kill_switch_reason = store.get_kv("kill_switch_reason")
        ts = store.get_kv("kill_switch_at")
        self.kill_switch_at = datetime.fromisoformat(ts) if ts else None

    def state(self) -> RiskState:
        return RiskState(kill_switch=self.kill_switch, kill_switch_reason=self.kill_switch_reason,
                         kill_switch_at=self.kill_switch_at, limits=list(self.limits.values()),
                         schedule=self.schedule, session_closed=self.session_closed(), addon_silent=self.addon_silent)

    def _today(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    def session_closed(self) -> bool:
        return self.schedule.enabled and bool(self.schedule.flatten_at) and self.session_closed_day == self._today()

    def _in_window(self) -> tuple[bool, str | None]:
        if not self.schedule.enabled:
            return True, None
        hm = self.now().strftime("%H:%M")
        if self.schedule.window_start and hm < self.schedule.window_start:
            return False, f"fuera de horario: las copias empiezan a las {self.schedule.window_start}"
        if self.session_closed():
            return False, f"sesión cerrada a las {self.schedule.flatten_at}; se reanuda mañana"
        if self.schedule.flatten_at and hm >= self.schedule.flatten_at:
            return False, f"fuera de horario: después de las {self.schedule.flatten_at} no se copia"
        return True, None

    # ---- horario ----
    def set_schedule(self, sched: Schedule) -> Schedule:
        sched.last_flatten_day = self.schedule.last_flatten_day
        self.schedule = sched
        self.store.set_kv("schedule", sched.model_dump_json())
        self.audit.log("SCHEDULE_SET", f"Horario {'activo' if sched.enabled else 'desactivado'}: inicio {sched.window_start or '-'}, "
                       f"cierre {sched.flatten_at or '-'}{' (incluye maestra)' if sched.include_master else ''}")
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        return sched

    def reopen_session(self) -> None:
        """Levanta a mano el bloqueo del cierre programado de hoy."""
        self.session_closed_day = ""
        self.store.set_kv("session_closed_day", "")
        self.audit.log("SESSION_REOPENED", "Bloqueo del cierre programado levantado manualmente")
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))

    async def check(self) -> None:
        """Se ejecuta tras cada sincronización de cuentas: heartbeat, pérdida diaria, cierre programado."""
        await self._check_heartbeat()
        await self._check_daily_loss()
        await self._check_schedule()

    async def _check_heartbeat(self) -> None:
        if self.bridge is None or self.bridge.health.mode != "ninja":
            return
        hb = self.bridge.health.last_heartbeat
        silent = hb is None or (datetime.now() - hb).total_seconds() > self.heartbeat_timeout
        if silent and not self.addon_silent:
            self.addon_silent = True
            self.audit.log("ADDON_SILENT", f"Sin heartbeat del addon de NinjaTrader desde hace más de {int(self.heartbeat_timeout)} s: "
                           "no se están recibiendo operaciones de la maestra")
            self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        elif not silent and self.addon_silent:
            self.addon_silent = False
            self.audit.log("ADDON_BACK", "Heartbeat del addon recuperado")
            self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))

    async def _check_daily_loss(self) -> None:
        if not self.accounts:
            return
        today = self._today()
        for acc, limit in list(self.limits.items()):
            if limit.max_daily_loss <= 0:
                continue
            snap = self.accounts.accounts.get(acc)
            if snap is None:
                continue
            pnl = snap.daily_pnl
            if pnl <= -limit.max_daily_loss and not limit.trading_halted:
                limit.trading_halted, limit.halted_reason, limit.halted_at = True, "daily_loss", datetime.now()
                self.store.save_risk_limit(limit)
                self.audit.log("DAILY_LOSS_LIMIT", f"{acc}: P&L del día {pnl:,.2f} alcanzó el límite de -{limit.max_daily_loss:,.2f}. "
                               "Cuenta pausada y cerrada.", target=acc, details={"pnl": pnl, "limit": limit.max_daily_loss})
                self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
                if snap.open_positions:
                    try:
                        await self.flatten(acc, "límite de pérdida diaria")
                    except Exception as exc:
                        self.audit.log("ERROR", f"No se pudo cerrar {acc} tras el límite diario: {exc}. ¡Revisa la cuenta a mano!", target=acc)
            elif pnl <= -0.8 * limit.max_daily_loss and (acc, today) not in self._warned_80 and not limit.trading_halted:
                self._warned_80.add((acc, today))
                self.audit.log("DAILY_LOSS_WARNING", f"{acc}: P&L del día {pnl:,.2f}, al 80 % del límite de -{limit.max_daily_loss:,.2f}",
                               target=acc)

    async def _check_schedule(self) -> None:
        s = self.schedule
        if not s.enabled or not s.flatten_at:
            return
        today = self._today()
        if s.last_flatten_day == today or self.now().strftime("%H:%M") < s.flatten_at:
            return
        s.last_flatten_day = today
        self.session_closed_day = today
        self.store.set_kv("schedule", s.model_dump_json())
        self.store.set_kv("session_closed_day", today)
        self.audit.log("SCHEDULED_FLATTEN", f"Cierre programado de las {s.flatten_at}: cerrando "
                       f"{'todas las cuentas' if s.include_master else 'las seguidoras'} y bloqueando copias hasta mañana")
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        await self.flatten_all(include_master=s.include_master, reason="cierre programado")

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
        prev = self.limits.get(limit.account_id)
        if prev and prev.trading_halted and prev.halted_reason == "daily_loss" and not limit.trading_halted \
                and limit.max_daily_loss > 0 and self.accounts:
            snap = self.accounts.accounts.get(limit.account_id)
            if snap and snap.daily_pnl <= -limit.max_daily_loss:
                raise ValueError(f"{limit.account_id} sigue con P&L {snap.daily_pnl:,.2f}, por debajo del límite de "
                                 f"-{limit.max_daily_loss:,.2f}: no se reanuda hoy (sube el límite si de verdad quieres seguir)")
        if limit.trading_halted and not limit.halted_reason:
            prev = self.limits.get(limit.account_id)
            if prev and prev.trading_halted:
                limit.halted_reason, limit.halted_at = prev.halted_reason, prev.halted_at
            else:
                limit.halted_at = datetime.now()
        if not limit.trading_halted:
            limit.halted_reason, limit.halted_at = "", None
        self.limits[limit.account_id] = limit
        self.store.save_risk_limit(limit)
        self.audit.log("RISK_LIMIT_SET", f"Límites {limit.account_id}: pérdida diaria máx {limit.max_daily_loss}, "
                       f"tamaño máx {limit.max_position_size}, halted={limit.trading_halted}", target=limit.account_id)
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        return limit

    def allows(self, account_id: str, quantity: int, symbol: str = "", action: str = "") -> tuple[bool, str | None]:
        """¿Se puede enviar una orden de `quantity` a `account_id`?"""
        if self.kill_switch:
            return False, "kill switch global activo"
        ok, reason = self._in_window()
        if not ok:
            return False, reason
        limit = self.limits.get(account_id)
        if limit is None:
            return True, None
        if limit.trading_halted:
            why = {"daily_loss": "límite de pérdida diaria"}.get(limit.halted_reason, "pausa manual")
            return False, f"cuenta {account_id} en pausa ({why})"
        if limit.max_position_size:
            if quantity > limit.max_position_size:
                return False, f"qty {quantity} supera el máximo {limit.max_position_size} de {account_id}"
            if self.accounts and symbol:
                pos = self.accounts.position(account_id, symbol)
                sign = 1 if action.upper().startswith("BUY") else -1
                resulting = abs(pos + sign * quantity)
                if resulting > limit.max_position_size and resulting > abs(pos):
                    return False, (f"posición resultante {resulting} supera el máximo {limit.max_position_size} de {account_id} "
                                   f"(actual {pos:+d})")
        return True, None
