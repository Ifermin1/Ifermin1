"""Avisos al teléfono por Telegram.

Se suscribe a la auditoría y manda por Telegram los eventos importantes (límites, cierres, rechazos, desincronización,
addon caído...). Agrupa lo que llega en 1,5 s en un solo mensaje y no repite el mismo aviso de la misma cuenta en un
minuto. Configuración en la consola (Riesgo → Avisos al teléfono): token del bot (BotFather) y chat; el chat se puede
detectar solo tras escribirle /start al bot. Si hay un manejador de órdenes (`commands`), el servicio sondea getUpdates y
atiende /estado, /cerrar, /pausar… solo del chat configurado.
"""
import asyncio
import json
import time
from typing import Awaitable, Callable

from loguru import logger
from pydantic import BaseModel, Field

from tradepilot.core.events import TOPIC_AUDIT, EventBus
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore

# tipo -> (icono, título corto). Lo que no está aquí se manda con el tipo tal cual si el usuario lo activa.
TITLES: dict[str, tuple[str, str]] = {
    "NEWS_SOON": ("📰", "Noticia a punto de salir"), "NEWS_RESULT": ("📊", "Dato publicado"),
    "MANUAL_ORDER": ("🟢", "Orden desde la consola"), "MANUAL_FILL": ("✅", "Orden manual ejecutada"), "MANUAL_REJECTED": ("⛔", "Orden manual rechazada"),
    "MANUAL_CANCEL": ("🚫", "Orden manual cancelada"), "MANUAL_MODIFY": ("✏️", "Orden manual modificada"),
    "PRESET_APPLIED": ("🎚️", "Preset de riesgo aplicado"), "DAY_CLOSED": ("⛔", "DÍA CERRADO: pérdida conjunta"), "DAY_LOSS_WARNING": ("⚠️", "Cerca del límite del día"), "AUTO_RISK_APPLIED": ("🛡️", "Perfil de riesgo aplicado solo"),
    "TRAIL_START": ("🎯", "Trailing stop activado"), "TRAIL_MOVE": ("↗️", "Trailing: stop movido"), "TRAIL_END": ("🏁", "Trailing terminado"),
    "DAILY_LOSS_LIMIT": ("🛑", "Límite de pérdida diaria"), "DAILY_LOSS_WARNING": ("⚠️", "Cerca del límite de pérdida"),
    "DAILY_PROFIT_TARGET": ("🎯", "Corte del día alcanzado"), "DAILY_PROFIT_WARNING": ("🟢", "Cerca del corte del día"),
    "PROFIT_GOAL": ("🏆", "Objetivo de la evaluación alcanzado"), "PROFIT_GOAL_WARNING": ("🟢", "Cerca del objetivo de la evaluación"),
    "DRAWDOWN_LIMIT": ("🛑", "Límite de drawdown"), "DRAWDOWN_WARNING": ("⚠️", "Drawdown al 80 %"),
    "KILL_SWITCH_ON": ("🛑", "Kill switch activado"), "KILL_SWITCH_OFF": ("▶️", "Replicación reanudada"),
    "FLATTEN": ("✂️", "Cierre de cuenta"), "SCHEDULED_FLATTEN": ("⏰", "Cierre programado"), "NAKED_CLOSE": ("🚨", "Stop rechazado: cuenta cerrada"),
    "FOLLOWER_REJECTED": ("❌", "Orden rechazada"), "ACCOUNT_LOCKED": ("🔒", "Cuenta bloqueada por el prop firm"),
    "DESYNC": ("🔀", "Seguidora desincronizada"), "RESYNC": ("✅", "Seguidora igualada"), "OVERCLOSE_FIX": ("🩹", "Sobrecierre corregido"),
    "ADDON_SILENT": ("📵", "Sin heartbeat del addon"), "ADDON_DOWN": ("📵", "Addon caído"), "ADDON_BACK": ("📶", "Addon de vuelta"),
    "ADDON_RESTART": ("🔁", "Addon reiniciado"), "GAP": ("📉", "Mensajes perdidos"), "ERROR": ("❗", "Error"),
    "DEGRADED_ON": ("🚨", "MODO DEGRADADO: sin addon"), "DEGRADED_OFF": ("✅", "Addon de vuelta"), "ADDON_STILL_DOWN": ("📵", "Sigue sin addon"),
    "MORNING_BRIEF": ("☀️", "Briefing de la mañana"), "SESSION_REVIEW": ("🏁", "Cierre de sesión"), "TILT": ("🧠", "Tilt: para un momento"), "TILT_RESUME": ("▶️", "Pausa por tilt levantada"),
    "COPILOT": ("🧭", "Copiloto: propuesta sobre una posición"), "ASSISTANT_RUN": ("🤖", "Asistente: plan ejecutado"), "ADDON_ALONE": ("🕳️", "El addon estuvo sin engine"), "ADDON_SELF_RESTART": ("🔧", "El addon se reconstruyó solo"),
    "NO_TICKS": ("📡", "Sin datos de mercado"), "TICKS_BACK": ("📶", "Datos de mercado de vuelta"), "ENGINE_RESUMED": ("⏰", "PC reanudado / reloj cambiado"), "RECONCILE": ("🧾", "Reconciliación"),
    "ENTRY_MISSED": ("🚫", "Entrada no ejecutada"), "BLOCKED": ("⛔", "Copia bloqueada"), "TRIMMED": ("✂️", "Copia recortada"),
    "FOLLOWER_FILL": ("💱", "Fill"), "MASTER_RECEIVED": ("📥", "Operación del maestro"), "ENGINE_START": ("🟦", "Engine arrancado"), "KILL_SWITCH_PERSISTED": ("🛑", "Kill switch sigue activo"),
}
DEFAULT_EVENTS = ["DAILY_LOSS_LIMIT", "DAILY_LOSS_WARNING", "DAILY_PROFIT_TARGET", "DAILY_PROFIT_WARNING", "PROFIT_GOAL", "DRAWDOWN_LIMIT",
                  "DRAWDOWN_WARNING", "KILL_SWITCH_ON", "KILL_SWITCH_OFF", "KILL_SWITCH_PERSISTED", "FLATTEN", "SCHEDULED_FLATTEN", "NAKED_CLOSE",
                  "FOLLOWER_REJECTED", "ACCOUNT_LOCKED", "DESYNC", "OVERCLOSE_FIX", "ADDON_SILENT", "ADDON_DOWN", "ADDON_BACK",
                  "ADDON_RESTART", "GAP", "ERROR", "ENTRY_MISSED", "NEWS_SOON", "NEWS_RESULT", "MANUAL_REJECTED", "DAY_CLOSED", "AUTO_RISK_APPLIED",
                  "DEGRADED_ON", "DEGRADED_OFF", "ADDON_STILL_DOWN", "NO_TICKS", "TICKS_BACK", "ENGINE_RESUMED", "ADDON_ALONE", "ADDON_SELF_RESTART", "COPILOT", "ASSISTANT_RUN", "MORNING_BRIEF", "SESSION_REVIEW", "TILT"]


class NotifyConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    events: list[str] = Field(default_factory=lambda: list(DEFAULT_EVENTS))


class NotifyService:
    def __init__(self, store: SQLiteStore | None, bus: EventBus) -> None:
        self.store = store
        self.bus = bus
        raw = store.get_kv("notifications") if store else None
        self.config = NotifyConfig.model_validate(json.loads(raw)) if raw else NotifyConfig()
        self.coalesce_s = 1.5
        self.dedupe_s = 60.0
        self.sender: Callable[[str, str, dict], Awaitable[dict]] = self._http   # (token, método, json) -> respuesta; inyectable
        self._pending: list[str] = []
        self._flush_task: asyncio.Task | None = None
        self._recent: dict[tuple, float] = {}
        self.stats = {"sent": 0, "errors": 0, "last_error": None, "last_sent_at": None, "suppressed": 0, "commands": 0}
        self.commands = None                      # objeto con `async handle(update)` (TelegramCommands); None = solo avisos
        self.poll_timeout_s = 25                  # long polling de Telegram
        self.poll_idle_s = 0.5
        self._poll_task: asyncio.Task | None = None
        self._offset = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        bus.subscribe(TOPIC_AUDIT, self._on_audit)

    # ---- ciclo de vida: sondeo de órdenes ----
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._ensure_poll()

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None

    def _want_poll(self) -> bool:
        return bool(self.commands is not None and self.config.enabled and self.config.bot_token and self.config.chat_id)

    def _ensure_poll(self) -> None:
        """Arranca o para el sondeo según la configuración. Se puede llamar desde otro hilo (rutas síncronas)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._ensure_poll)
            return
        running = self._poll_task is not None and not self._poll_task.done()
        if self._want_poll() and not running:
            self._poll_task = loop.create_task(self._poll_loop())
        elif not self._want_poll() and running:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self) -> None:
        logger.info("Telegram: atendiendo órdenes (/estado, /cerrar, /pausar, /reanudar, /breakeven)")
        token = self.config.bot_token
        while True:
            try:
                r = await self.sender(token, "getUpdates", {"offset": self._offset, "timeout": self.poll_timeout_s, "allowed_updates": ["message", "callback_query"]})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Telegram: fallo sondeando órdenes: {exc}")
                await asyncio.sleep(5)
                continue
            if not r.get("ok", False):
                logger.warning(f"Telegram: getUpdates rechazado: {r.get('description')}")
                await asyncio.sleep(10)
                continue
            for u in r.get("result", []):
                self._offset = max(self._offset, int(u.get("update_id", 0)) + 1)
                if self.commands is not None:
                    self.stats["commands"] += 1
                    await self.commands.handle(u)
            await asyncio.sleep(self.poll_idle_s)

    # ---- configuración ----
    def set_config(self, cfg: NotifyConfig) -> NotifyConfig:
        cfg.bot_token, cfg.chat_id = cfg.bot_token.strip(), cfg.chat_id.strip()
        cfg.events = sorted({e.strip().upper() for e in cfg.events if e.strip()})
        restart = cfg.bot_token != self.config.bot_token
        self.config = cfg
        if self.store:
            self.store.set_kv("notifications", cfg.model_dump_json())
        if restart and self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        self._ensure_poll()
        return cfg

    def state(self) -> dict:
        return {**self.config.model_dump(), "stats": self.stats, "titles": {k: v[1] for k, v in TITLES.items()}, "defaults": DEFAULT_EVENTS}

    # ---- entrada: auditoría ----
    async def _on_audit(self, event: dict) -> None:
        if not self.config.enabled or not self.config.bot_token or not self.config.chat_id:
            return
        etype = str(event.get("event_type", ""))
        if etype not in self.config.events:
            return
        target = event.get("target_account") or event.get("source_account") or ""
        key = (etype, target, str(event.get("message", ""))[:80])
        now = time.monotonic()
        if now - self._recent.get(key, -1e9) < self.dedupe_s:
            self.stats["suppressed"] += 1
            return
        self._recent[key] = now
        if len(self._recent) > 500:
            for k in sorted(self._recent, key=self._recent.get)[:250]:
                del self._recent[k]
        self._pending.append(self.format(event))
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.get_running_loop().create_task(self._flush_later())

    @staticmethod
    def format(event: dict) -> str:
        etype = str(event.get("event_type", ""))
        icon, title = TITLES.get(etype, ("•", etype.replace("_", " ").capitalize()))
        target = event.get("target_account") or ""
        head = f"{icon} <b>{_esc(title)}</b>" + (f" · {_esc(str(target))}" if target else "")
        return head + "\n" + _esc(str(event.get("message", "")))

    async def _flush_later(self) -> None:
        await asyncio.sleep(self.coalesce_s)
        batch, self._pending = self._pending, []
        if batch:
            await self.send("\n\n".join(batch))

    # ---- salida: Telegram ----
    async def api(self, method: str, payload: dict) -> dict:
        """Llamada directa a la API del bot (teclados, respuestas a botones, editar mensajes)."""
        return await self.sender(self.config.bot_token, method, payload)

    async def send(self, text: str) -> bool:
        try:
            r = await self.sender(self.config.bot_token, "sendMessage",
                                  {"chat_id": self.config.chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": True})
            if not r.get("ok", False):
                raise RuntimeError(str(r.get("description") or r))
            self.stats["sent"] += 1
            self.stats["last_sent_at"] = time.time()
            self.stats["last_error"] = None
            return True
        except Exception as exc:
            self.stats["errors"] += 1
            self.stats["last_error"] = str(exc)[:200]
            logger.warning(f"Telegram: no se pudo enviar el aviso: {exc}")
            return False

    async def test(self) -> dict:
        if not self.config.bot_token or not self.config.chat_id:
            return {"ok": False, "error": "falta el token del bot o el chat"}
        ok = await self.send("✅ <b>TradePilot X</b>: los avisos al teléfono funcionan. Recibirás aquí límites, cierres, rechazos y caídas del addon.")
        return {"ok": ok, "error": None if ok else self.stats["last_error"]}

    async def discover_chat(self, bot_token: str | None = None) -> dict:
        """Tras escribir /start al bot, getUpdates trae el chat: se devuelve el último para no tener que buscar el id a mano."""
        token = (bot_token or self.config.bot_token).strip()
        if not token:
            return {"ok": False, "error": "falta el token del bot"}
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None
        try:
            r = await self.sender(token, "getUpdates", {"limit": 20})
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}
        finally:
            self._ensure_poll()
        if not r.get("ok", False):
            return {"ok": False, "error": str(r.get("description") or "Telegram no respondió")}
        chats = [u.get("message", {}).get("chat") or u.get("channel_post", {}).get("chat") for u in r.get("result", [])]
        chats = [c for c in chats if c and c.get("id") is not None]
        if not chats:
            return {"ok": False, "error": "el bot no ha recibido ningún mensaje: escríbele /start en Telegram y vuelve a probar"}
        c = chats[-1]
        name = " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) or c.get("title") or c.get("username") or ""
        return {"ok": True, "chat_id": str(c["id"]), "name": name}

    @staticmethod
    async def _http(token: str, method: str, payload: dict) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
            try:
                return r.json()
            except ValueError:
                return {"ok": False, "description": f"HTTP {r.status_code}"}


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
