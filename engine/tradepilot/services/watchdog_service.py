"""Vigilante del engine: suspensión del PC, saltos de reloj, modo degradado y mercado sin datos.

Cada pocos segundos comprueba:
- **Suspensión / salto de reloj**: si el reloj del PC avanzó mucho más que el tiempo que el engine estuvo despierto (el PC
  durmió, hibernó o cambió la hora), lo audita (ENGINE_RESUMED), vuelve a sincronizar con NinjaTrader y reconcilia.
- **Modo degradado**: en modo ninja, cuando el addon no late (ADDON_SILENT) o el puente está caído durante más de
  DEGRADED_AFTER_S, el engine se declara degradado (DEGRADED_ON): no copia, no puede operar ni cerrar (sin addon no hay
  camino al bróker) y lo recuerda cada REMIND_S (ADDON_STILL_DOWN) hasta que vuelve (DEGRADED_OFF). La consola lo muestra en
  rojo y los avisos (Telegram/push) llegan al teléfono.
- **Sin datos de mercado**: con el puente conectado y el mercado abierto (CME: domingo 18:00 a viernes 17:00 hora de Nueva
  York, con pausa diaria de 17:00 a 18:00), si no llega ningún tick durante NO_TICKS_AFTER_S se avisa (NO_TICKS): casi
  siempre es NinjaTrader desconectado del proveedor de datos. Se recuerda cada REMIND_S y se cierra con TICKS_BACK.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

from loguru import logger

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:          # Windows sin el paquete tzdata: no hay base de zonas horarias
    NY = None


def _us_dst(d_utc: datetime) -> bool:
    """Horario de verano de EE. UU.: del segundo domingo de marzo al primer domingo de noviembre (2:00 locales)."""
    y = d_utc.year
    march = datetime(y, 3, 1, tzinfo=timezone.utc)
    start = march + timedelta(days=(6 - march.weekday()) % 7 + 7, hours=7)       # 2:00 EST = 7:00 UTC
    nov = datetime(y, 11, 1, tzinfo=timezone.utc)
    end = nov + timedelta(days=(6 - nov.weekday()) % 7, hours=6)                  # 2:00 EDT = 6:00 UTC
    return start <= d_utc < end


def to_ny(d_utc: datetime) -> datetime:
    """Hora de Nueva York; sin base de zonas horarias se calcula el desfase por regla (−4 h en verano, −5 h en invierno)."""
    if NY is not None:
        return d_utc.astimezone(NY)
    return d_utc.astimezone(timezone(timedelta(hours=-4 if _us_dst(d_utc) else -5)))


def cme_open(now_utc: datetime | None = None) -> bool:
    """Horario de los futuros de CME (ES/NQ/CL/GC…): domingo 18:00 → viernes 17:00 ET, con pausa diaria 17:00-18:00 ET."""
    d = to_ny(now_utc or datetime.now(timezone.utc))
    wd, h, m = d.weekday(), d.hour, d.minute
    minutes = h * 60 + m
    if wd == 5:                        # sábado
        return False
    if wd == 6:                        # domingo: abre a las 18:00
        return minutes >= 18 * 60
    if wd == 4 and minutes >= 17 * 60:  # viernes: cierra a las 17:00
        return False
    return not (17 * 60 <= minutes < 18 * 60)


class WatchdogService:
    def __init__(self, accounts, risk, bridge, audit, bus, market=None, reconcile=None, mode: str = "mock",
                 interval_s: float = 10.0, degraded_after_s: float = 30.0, no_ticks_after_s: float = 180.0, remind_s: float = 600.0) -> None:
        self.accounts, self.risk, self.bridge, self.audit, self.bus, self.market, self.reconcile = accounts, risk, bridge, audit, bus, market, reconcile
        self.mode = mode
        self.interval_s, self.degraded_after_s, self.no_ticks_after_s, self.remind_s = interval_s, degraded_after_s, no_ticks_after_s, remind_s
        self.market_open = cme_open                       # sustituible en pruebas
        self.now = time.time                              # reloj de pared, sustituible en pruebas
        self.mono = time.monotonic
        self._task: asyncio.Task | None = None
        self._last_wall = self.now()
        self._last_mono = self.mono()
        self.started_at = self.now()
        # estado
        self.degraded = False
        self.degraded_since: float | None = None
        self._down_since: float | None = None
        self._last_remind: float = 0.0
        self.no_ticks = False
        self._no_ticks_since: float | None = None
        self._last_ticks_remind: float = 0.0
        self.last_resume: dict | None = None
        self.loop_lag_ms = 0
        self.ticks = 0

    # ---- ciclo de vida ----
    async def start(self) -> None:
        self._last_wall, self._last_mono = self.now(), self.mono()
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Vigilante: error en el ciclo: {exc}")

    # ---- un ciclo ----
    async def tick(self) -> None:
        self.ticks += 1
        await self._check_resume()
        self._check_degraded()
        self._check_ticks()

    async def _check_resume(self) -> None:
        wall, mono = self.now(), self.mono()
        d_wall, d_mono = wall - self._last_wall, mono - self._last_mono
        self._last_wall, self._last_mono = wall, mono
        self.loop_lag_ms = int(max(0.0, d_mono - self.interval_s) * 1000)
        gap = d_wall - d_mono
        if abs(gap) < 60 and d_mono < self.interval_s + 60:
            return
        if abs(gap) >= 60:
            kind = "suspensión" if gap > 0 else "reloj atrasado"
            msg = (f"El PC estuvo {'suspendido o sin ejecutar el engine' if gap > 0 else 'con el reloj cambiado'} ({abs(gap) / 60:.0f} min): "
                   "se vuelve a sincronizar con NinjaTrader y a reconciliar")
        else:
            kind = "bloqueo"
            msg = f"El engine estuvo {d_mono / 60:.0f} min sin ejecutar su ciclo (PC saturado o proceso congelado): se vuelve a sincronizar y a reconciliar"
        self.last_resume = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "kind": kind, "gap_s": round(gap), "stalled_s": round(d_mono)}
        self.audit.log("ENGINE_RESUMED", msg, details=self.last_resume)
        try:
            await self.accounts.sync_once()
        except Exception as exc:
            logger.warning(f"Vigilante: no se pudo resincronizar tras la reanudación: {exc}")
        if self.reconcile is not None:
            try:
                await self.reconcile.run("reanudación")
            except Exception as exc:
                logger.warning(f"Vigilante: reconciliación tras la reanudación falló: {exc}")

    def _bridge_down(self) -> bool:
        if self.mode != "ninja":
            return False
        h = self.bridge.health if self.bridge is not None else None
        return bool(self.risk.addon_silent) or (h is not None and not h.connected)

    def _check_degraded(self) -> None:
        now = self.now()
        down = self._bridge_down()
        if down:
            if self._down_since is None:
                self._down_since = now
            if not self.degraded and now - self._down_since >= self.degraded_after_s:
                self.degraded, self.degraded_since, self._last_remind = True, self._down_since, now
                self.audit.log("DEGRADED_ON", "MODO DEGRADADO: sin addon de NinjaTrader. No se copia ni se puede operar o cerrar desde aquí hasta que vuelva. "
                               "Revisa que NinjaTrader esté abierto, el addon cargado (F5) y la conexión con el bróker.", details={"since": self.degraded_since})
                self._publish()
            elif self.degraded and now - self._last_remind >= self.remind_s:
                self._last_remind = now
                mins = (now - (self.degraded_since or now)) / 60
                exposure = [f"{a.account_id} {'L' if p.quantity > 0 else 'C'}{abs(p.quantity)} {p.symbol}" for a in self.accounts.accounts.values() if a.enabled for p in a.open_positions if p.quantity]
                self.audit.log("ADDON_STILL_DOWN", f"Sigue sin addon desde hace {mins:.0f} min" + (f" · posiciones abiertas sin vigilancia: {', '.join(exposure[:6])}" if exposure else " · sin posiciones abiertas"),
                               details={"minutes": round(mins), "exposure": exposure})
        else:
            self._down_since = None
            if self.degraded:
                mins = (now - (self.degraded_since or now)) / 60
                self.degraded, self.degraded_since = False, None
                self.audit.log("DEGRADED_OFF", f"Addon de vuelta tras {mins:.0f} min: se sale del modo degradado y se reconcilia", details={"minutes": round(mins)})
                self._publish()
                if self.reconcile is not None:
                    asyncio.get_running_loop().create_task(self._reconcile_safe("vuelta del addon"))

    async def _reconcile_safe(self, reason: str) -> None:
        try:
            await self.accounts.sync_once()
            await self.reconcile.run(reason)
        except Exception as exc:
            logger.warning(f"Vigilante: reconciliación ({reason}) falló: {exc}")

    def _last_tick_age(self) -> float | None:
        if self.market is None or not self.market.last_at:
            return None
        return max(0.0, self.now() - max(self.market.last_at.values()))

    def _check_ticks(self) -> None:
        now = self.now()
        h = self.bridge.health if self.bridge is not None else None
        connected = self.mode == "ninja" and h is not None and h.connected and not self.risk.addon_silent
        age = self._last_tick_age()
        # sin ticks nunca: el addon aún no ha suscrito nada; se cuenta desde el arranque
        stale = connected and self.market_open() and (age is None and now - self.started_at > self.no_ticks_after_s or age is not None and age > self.no_ticks_after_s)
        if stale:
            if self._no_ticks_since is None:
                self._no_ticks_since = now
            if not self.no_ticks:
                self.no_ticks, self._last_ticks_remind = True, now
                self.audit.log("NO_TICKS", f"Mercado abierto y NinjaTrader conectado, pero sin datos de precio desde hace {(age or (now - self.started_at)) / 60:.0f} min: "
                               "revisa la conexión del proveedor de datos en NinjaTrader (Control Center → Connections)")
                self._publish()
            elif now - self._last_ticks_remind >= self.remind_s:
                self._last_ticks_remind = now
                self.audit.log("NO_TICKS", f"Sigue sin datos de precio desde hace {(now - self._no_ticks_since) / 60:.0f} min")
        else:
            if self.no_ticks and (age is not None and age <= self.no_ticks_after_s):
                self.no_ticks = False
                self.audit.log("TICKS_BACK", "Datos de precio de vuelta")
                self._publish()
            if not stale:
                self._no_ticks_since = None if age is not None and age <= self.no_ticks_after_s else self._no_ticks_since

    def _publish(self) -> None:
        try:
            from tradepilot.core.events import TOPIC_RISK
            self.bus.publish_nowait(TOPIC_RISK, self.risk.state().model_dump(mode="json"))
        except Exception:
            pass

    def state(self) -> dict:
        age = self._last_tick_age()
        return {"degraded": self.degraded, "degraded_since": datetime.fromtimestamp(self.degraded_since).astimezone().isoformat(timespec="seconds") if self.degraded_since else None,
                "no_ticks": self.no_ticks, "market_open": self.market_open(), "last_tick_age_s": round(age) if age is not None else None,
                "last_resume": self.last_resume, "loop_lag_ms": self.loop_lag_ms, "ticks": self.ticks}
