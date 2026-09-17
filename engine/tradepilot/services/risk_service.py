import json
from datetime import datetime, timedelta

from loguru import logger

from tradepilot.core.events import TOPIC_RISK, EventBus
from tradepilot.domain.risk import Commissions, RiskLimit, RiskState, Schedule
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
        self.commissions = None               # CommissionService, lo inyecta el contenedor
        self.master_flatten_grace = 20.0      # segundos sin copiar fills de la maestra tras cerrarla
        raw = store.get_kv("schedule")
        self.schedule = Schedule.model_validate(json.loads(raw)) if raw else Schedule()
        self.session_closed_day: str = store.get_kv("session_closed_day", "") or ""
        self.heartbeat_timeout = 15.0   # el addon late cada 5 s
        self.addon_silent = False
        self._silent_since: datetime | None = None
        self._last_resubscribe: datetime | None = None
        self.resubscribe_every = 30.0
        self.stale_after = 2            # comprobaciones seguidas (una por sincronización) con eventos que no llegan
        self.quiet_seconds = 3.0        # sin ningún mensaje del addon durante este tiempo = canal sospechoso
        self._stale_checks = 0
        self._started_at = datetime.now()
        self._warned_80: set[tuple[str, str, str]] = set()   # (cuenta, día, loss|profit) avisadas al 80 %
        self._dd_warned: set[str] = set()                     # cuentas avisadas al 80 % del drawdown (se rearma al bajar del 50 %)
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
                         schedule=self.schedule, session_closed=self.session_closed(), addon_silent=self.addon_silent,
                         commissions=self.commissions.config if self.commissions is not None else Commissions())

    def set_commissions(self, cfg: Commissions) -> Commissions:
        if self.commissions is None:
            raise RuntimeError("sin servicio de comisiones")
        cfg = self.commissions.set_config(cfg)
        self.audit.log("COMMISSIONS_SET", f"Comisiones {'activadas' if cfg.enabled else 'desactivadas'}: {cfg.default_per_side} $/contrato/lado por defecto"
                       + (", " + ", ".join(f"{k} {v}" for k, v in sorted(cfg.rates.items())) if cfg.rates else ""))
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        return cfg

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
        await self._check_event_channel()
        await self._check_daily_limits()
        await self._check_drawdown()
        await self._check_schedule()

    async def _check_heartbeat(self) -> None:
        if self.bridge is None or self.bridge.health.mode != "ninja":
            return
        hb = self.bridge.health.last_heartbeat
        now = datetime.now()
        if hb is None:
            # al arrancar, dar al addon el tiempo de un heartbeat antes de declararlo mudo
            if (now - self._started_at).total_seconds() < self.heartbeat_timeout:
                return
        silent = hb is None or (now - hb).total_seconds() > self.heartbeat_timeout
        if silent and not self.addon_silent:
            self.addon_silent = True
            self._silent_since = now
            self.audit.log("ADDON_SILENT", f"Sin heartbeat del addon de NinjaTrader desde hace más de {int(self.heartbeat_timeout)} s: "
                           "no se están recibiendo operaciones de la maestra")
            self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        elif not silent and self.addon_silent:
            self.addon_silent = False
            self._silent_since = None
            self.audit.log("ADDON_BACK", "Heartbeat del addon recuperado")
            self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        if silent and (self._last_resubscribe is None or (now - self._last_resubscribe).total_seconds() >= self.resubscribe_every):
            # Autocuración: si el addon responde a comandos pero no llegan eventos, reconectar el canal de eventos.
            self._last_resubscribe = now
            try:
                alive = await self.bridge.ping()
            except Exception:
                alive = False
            if alive:
                await self.bridge.resubscribe()
                self.audit.log("RESUBSCRIBE", "El addon responde a comandos pero no envía eventos: canal de eventos reconectado")
                await self.on_addon_restart("Canal de eventos reconectado tras el silencio del addon")
            else:
                self.audit.log("ADDON_DOWN", "El addon tampoco responde a comandos (PING): NinjaTrader cerrado o addon no cargado")

    async def _check_event_channel(self) -> None:
        """Detección rápida de un canal de eventos atascado: por el canal de comandos el addon (>= 1.8) dice qué
        arranque es y cuántos mensajes lleva publicados; si publica y aquí no llega nada, se reconecta el canal
        en segundos en vez de esperar al vigilante del heartbeat (15 s)."""
        if self.bridge is None or self.replication is None:
            return
        try:
            alive, boot, seq = await self.bridge.ping_state()
        except Exception:
            return
        if not alive or (boot is None and seq is None):
            self._stale_checks = 0
            return
        h = self.bridge.health
        h.addon_boot_req, h.addon_seq_req = boot, seq
        now = datetime.now()
        last_in = h.last_msg_in
        if last_in is not None:
            quiet = (now - last_in).total_seconds() >= self.quiet_seconds
        else:
            quiet = (now - self._started_at).total_seconds() >= 10.0
        sub_seq = self.replication.last_seq
        boot_mismatch = bool(boot) and bool(h.addon_boot) and boot != h.addon_boot
        seq_ahead = seq is not None and (sub_seq is None or seq > sub_seq)
        stale = quiet and (boot_mismatch or seq_ahead)
        if not stale:
            self._stale_checks = 0
            return
        self._stale_checks += 1
        if self._stale_checks < self.stale_after:
            return
        self._stale_checks = 0
        await self.bridge.resubscribe()
        why = ("el addon se reinició" if boot_mismatch else f"el addon lleva publicados {seq} mensajes y aquí el último recibido es {sub_seq}")
        self.audit.log("RESUBSCRIBE", f"Los eventos del addon no estaban llegando ({why}): canal de eventos reconectado",
                       details={"boot": boot, "seq": seq, "last_seq": sub_seq})
        await self.on_addon_restart("Canal de eventos reconectado")

    async def on_addon_restart(self, detail: str) -> None:
        """Tras un reinicio del addon o una reconexión: volver a registrar las seguidoras (el addon olvidó sus WATCH)
        y avisar de que pudieron perderse operaciones; la vigilancia de posiciones marcará DESYNC si fue así."""
        followers = self.follower_accounts()
        if self.accounts is not None:
            self.accounts.forget_watches()
            for acc in followers:
                await self.accounts.watch(acc)
        self.audit.log("ADDON_RECOVERY", f"{detail}. Seguidoras registradas de nuevo ({', '.join(followers) or 'ninguna'}). "
                       "Si la maestra operó mientras tanto, la seguidora aparecerá DESINCRONIZADA: iguálala o ciérrala")
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))

    async def _check_daily_limits(self) -> None:
        """Pérdida máxima y objetivo de ganancia del día por cuenta (P&L realizado + flotante del addon)."""
        if not self.accounts:
            return
        today = self._today()
        for acc, limit in list(self.limits.items()):
            if limit.max_daily_loss <= 0 and limit.max_daily_profit <= 0:
                continue
            snap = self.accounts.accounts.get(acc)
            if snap is None or limit.trading_halted:
                continue
            pnl = snap.net_pnl      # neto: bruto de NinjaTrader menos las comisiones estimadas del día
            fees = f" (bruto {snap.daily_pnl:,.2f}, comisiones {snap.commissions_today:,.2f})" if snap.commissions_today else ""
            if limit.max_daily_loss > 0 and pnl <= -limit.max_daily_loss:
                await self._halt_daily(limit, snap, "daily_loss", "DAILY_LOSS_LIMIT",
                                       f"{acc}: P&L neto del día {pnl:,.2f}{fees} alcanzó el límite de -{limit.max_daily_loss:,.2f}. "
                                       "Cuenta pausada y cerrada.", "límite de pérdida diaria", limit.max_daily_loss)
            elif limit.max_daily_profit > 0 and pnl >= limit.max_daily_profit:
                await self._halt_daily(limit, snap, "daily_profit", "DAILY_PROFIT_TARGET",
                                       f"{acc}: P&L neto del día {pnl:,.2f}{fees} alcanzó el objetivo de +{limit.max_daily_profit:,.2f}. "
                                       "Cuenta pausada y cerrada para asegurar la ganancia.", "objetivo de ganancia diaria",
                                       limit.max_daily_profit)
            elif limit.max_daily_loss > 0 and pnl <= -0.8 * limit.max_daily_loss and (acc, today, "loss") not in self._warned_80:
                self._warned_80.add((acc, today, "loss"))
                self.audit.log("DAILY_LOSS_WARNING", f"{acc}: P&L neto del día {pnl:,.2f}{fees}, al 80 % del límite de -{limit.max_daily_loss:,.2f}",
                               target=acc)
            elif limit.max_daily_profit > 0 and pnl >= 0.8 * limit.max_daily_profit and (acc, today, "profit") not in self._warned_80:
                self._warned_80.add((acc, today, "profit"))
                self.audit.log("DAILY_PROFIT_WARNING", f"{acc}: P&L neto del día {pnl:,.2f}{fees}, al 80 % del objetivo de +{limit.max_daily_profit:,.2f}",
                               target=acc)

    async def _check_drawdown(self) -> None:
        """Drawdown dinámico del prop firm: la cuenta no puede caer más de `max_trailing_drawdown` desde el máximo que llegó a
        valer (AccountService lleva la marca de agua). Aviso al 80 % consumido; pausa y cierre cuando faltan
        `drawdown_buffer` USD (o menos) para el suelo, es decir, antes de que el prop firm cierre la cuenta."""
        if not self.accounts:
            return
        for acc, limit in list(self.limits.items()):
            if limit.max_trailing_drawdown <= 0:
                self._dd_warned.discard(acc)
                continue
            snap = self.accounts.accounts.get(acc)
            if snap is None or not snap.reported:
                continue
            dd = snap.drawdown
            if dd.floor is None or dd.room is None:
                continue
            if limit.trading_halted:
                continue
            if dd.room <= limit.drawdown_buffer:
                await self._halt_daily(limit, snap, "drawdown", "DRAWDOWN_LIMIT",
                                       f"{acc}: vale {dd.equity:,.2f}, a {dd.room:,.2f} del suelo {dd.floor:,.2f} del drawdown "
                                       f"(máximo {dd.peak:,.2f} − {limit.max_trailing_drawdown:,.2f}"
                                       + (f", colchón {limit.drawdown_buffer:,.2f}" if limit.drawdown_buffer else "")
                                       + "). Cuenta pausada y cerrada antes de que el prop firm la cierre.",
                                       "límite de drawdown", limit.max_trailing_drawdown,
                                       extra={"equity": dd.equity, "peak": dd.peak, "floor": dd.floor, "room": dd.room})
            elif (dd.pct or 0) >= 80 and acc not in self._dd_warned:
                self._dd_warned.add(acc)
                self.audit.log("DRAWDOWN_WARNING", f"{acc}: drawdown al {dd.pct:.0f} % ({dd.drawdown:,.2f} de {limit.max_trailing_drawdown:,.2f} "
                               f"desde el máximo {dd.peak:,.2f}); quedan {dd.room:,.2f} hasta el suelo {dd.floor:,.2f}",
                               target=acc, details={"equity": dd.equity, "peak": dd.peak, "floor": dd.floor, "room": dd.room, "pct": dd.pct})
            elif (dd.pct or 0) < 50:
                self._dd_warned.discard(acc)

    async def _halt_daily(self, limit: RiskLimit, snap, reason: str, event: str, message: str, why: str, value: float,
                          extra: dict | None = None) -> None:
        acc = limit.account_id
        limit.trading_halted, limit.halted_reason, limit.halted_at = True, reason, datetime.now()
        self.store.save_risk_limit(limit)
        self.audit.log(event, message, target=acc, details={"pnl": snap.net_pnl, "gross_pnl": snap.daily_pnl,
                                                            "commissions": snap.commissions_today, "limit": value, **(extra or {})})
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        if snap.open_positions:
            try:
                await self.flatten(acc, why)
            except Exception as exc:
                self.audit.log("ERROR", f"No se pudo cerrar {acc} tras el {why}: {exc}. ¡Revisa la cuenta a mano!", target=acc)

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
        snap = self.accounts.accounts.get(limit.account_id) if self.accounts else None
        if prev and prev.trading_halted and not limit.trading_halted and snap:
            if prev.halted_reason == "daily_loss" and limit.max_daily_loss > 0 and snap.net_pnl <= -limit.max_daily_loss:
                raise ValueError(f"{limit.account_id} sigue con P&L neto {snap.net_pnl:,.2f}, por debajo del límite de "
                                 f"-{limit.max_daily_loss:,.2f}: no se reanuda hoy (sube el límite si de verdad quieres seguir)")
            if prev.halted_reason == "daily_profit" and limit.max_daily_profit > 0 and snap.net_pnl >= limit.max_daily_profit:
                raise ValueError(f"{limit.account_id} sigue con P&L neto {snap.net_pnl:,.2f}, por encima del objetivo de "
                                 f"+{limit.max_daily_profit:,.2f}: no se reanuda hoy (sube el objetivo o quítalo si de verdad quieres seguir)")
            if prev.halted_reason == "drawdown" and limit.max_trailing_drawdown > 0:
                peak = self.accounts.peak_for(limit.account_id, limit.drawdown_mode)
                if peak is None:
                    peak = snap.drawdown.peak
                floor = peak - limit.max_trailing_drawdown
                if limit.drawdown_floor_cap > 0:
                    floor = min(floor, limit.drawdown_floor_cap)
                value = snap.balance if limit.drawdown_mode == "closed" else snap.drawdown.equity
                if value - floor <= limit.drawdown_buffer:
                    raise ValueError(f"{limit.account_id} sigue a {value - floor:,.2f} del suelo del drawdown ({floor:,.2f}): no se "
                                     "reanuda (sube el límite, baja el colchón o ajusta el máximo si de verdad quieres seguir)")
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
        if self.accounts:
            self.accounts.refresh_drawdown(limit.account_id)
        self.audit.log("RISK_LIMIT_SET", f"Límites {limit.account_id}: pérdida diaria máx {limit.max_daily_loss}, "
                       f"objetivo de ganancia {limit.max_daily_profit}, tamaño máx {limit.max_position_size}, "
                       f"drawdown máx {limit.max_trailing_drawdown} ({ {'intraday': 'dinámico', 'eod': 'EOD', 'closed': 'solo cerrado'}.get(limit.drawdown_mode, limit.drawdown_mode) }"
                       + (f", suelo bloqueado en {limit.drawdown_floor_cap}" if limit.drawdown_floor_cap else "")
                       + (f", colchón {limit.drawdown_buffer}" if limit.drawdown_buffer else "") + "), "
                       f"halted={limit.trading_halted}", target=limit.account_id)
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        return limit

    def delete_limit(self, account_id: str) -> bool:
        """Quita los límites de una cuenta (vuelve a copiar sin tope y sin pausa)."""
        limit = self.limits.pop(account_id, None)
        self.store.delete_risk_limit(account_id)
        if limit is None:
            return False
        if self.accounts:
            self.accounts.refresh_drawdown(account_id)
        self.audit.log("RISK_LIMIT_REMOVED", f"Límites de {account_id} eliminados: copia sin tope de pérdida, objetivo ni tamaño"
                       + (" (estaba en pausa: se reanuda)" if limit.trading_halted else ""), target=account_id)
        self.bus.publish_nowait(TOPIC_RISK, self.state().model_dump(mode="json"))
        return True

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
            why = {"daily_loss": "límite de pérdida diaria",
                   "daily_profit": "objetivo de ganancia diaria alcanzado",
                   "drawdown": "límite de drawdown"}.get(limit.halted_reason, "pausa manual")
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
