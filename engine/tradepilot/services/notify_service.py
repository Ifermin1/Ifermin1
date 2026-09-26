"""Avisos al teléfono por Telegram.

Se suscribe a la auditoría y manda por Telegram los eventos importantes (límites, cierres, rechazos, desincronización,
addon caído...). Agrupa lo que llega en 1,5 s en un solo mensaje y no repite el mismo aviso de la misma cuenta en un
minuto. Configuración en la consola (Riesgo → Avisos al teléfono): token del bot (BotFather) y chat; el chat se puede
detectar solo tras escribirle /start al bot.
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
    "DAILY_LOSS_LIMIT": ("🛑", "Límite de pérdida diaria"), "DAILY_LOSS_WARNING": ("⚠️", "Cerca del límite de pérdida"),
    "DAILY_PROFIT_TARGET": ("🎯", "Objetivo de ganancia alcanzado"), "DAILY_PROFIT_WARNING": ("🟢", "Cerca del objetivo de ganancia"),
    "DRAWDOWN_LIMIT": ("🛑", "Límite de drawdown"), "DRAWDOWN_WARNING": ("⚠️", "Drawdown al 80 %"),
    "KILL_SWITCH_ON": ("🛑", "Kill switch activado"), "KILL_SWITCH_OFF": ("▶️", "Replicación reanudada"),
    "FLATTEN": ("✂️", "Cierre de cuenta"), "SCHEDULED_FLATTEN": ("⏰", "Cierre programado"), "NAKED_CLOSE": ("🚨", "Stop rechazado: cuenta cerrada"),
    "FOLLOWER_REJECTED": ("❌", "Orden rechazada"), "ACCOUNT_LOCKED": ("🔒", "Cuenta bloqueada por el prop firm"),
    "DESYNC": ("🔀", "Seguidora desincronizada"), "RESYNC": ("✅", "Seguidora igualada"), "OVERCLOSE_FIX": ("🩹", "Sobrecierre corregido"),
    "ADDON_SILENT": ("📵", "Sin heartbeat del addon"), "ADDON_DOWN": ("📵", "Addon caído"), "ADDON_BACK": ("📶", "Addon de vuelta"),
    "ADDON_RESTART": ("🔁", "Addon reiniciado"), "GAP": ("📉", "Mensajes perdidos"), "ERROR": ("❗", "Error"),
    "ENTRY_MISSED": ("🚫", "Entrada no ejecutada"), "BLOCKED": ("⛔", "Copia bloqueada"), "TRIMMED": ("✂️", "Copia recortada"),
    "FOLLOWER_FILL": ("💱", "Fill"), "MASTER_RECEIVED": ("📥", "Operación del maestro"), "ENGINE_START": ("🟦", "Engine arrancado"),
}
DEFAULT_EVENTS = ["DAILY_LOSS_LIMIT", "DAILY_LOSS_WARNING", "DAILY_PROFIT_TARGET", "DAILY_PROFIT_WARNING", "DRAWDOWN_LIMIT",
                  "DRAWDOWN_WARNING", "KILL_SWITCH_ON", "KILL_SWITCH_OFF", "FLATTEN", "SCHEDULED_FLATTEN", "NAKED_CLOSE",
                  "FOLLOWER_REJECTED", "ACCOUNT_LOCKED", "DESYNC", "OVERCLOSE_FIX", "ADDON_SILENT", "ADDON_DOWN", "ADDON_BACK",
                  "ADDON_RESTART", "GAP", "ERROR", "ENTRY_MISSED"]


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
        self.stats = {"sent": 0, "errors": 0, "last_error": None, "last_sent_at": None, "suppressed": 0}
        bus.subscribe(TOPIC_AUDIT, self._on_audit)

    # ---- configuración ----
    def set_config(self, cfg: NotifyConfig) -> NotifyConfig:
        cfg.bot_token, cfg.chat_id = cfg.bot_token.strip(), cfg.chat_id.strip()
        cfg.events = sorted({e.strip().upper() for e in cfg.events if e.strip()})
        self.config = cfg
        if self.store:
            self.store.set_kv("notifications", cfg.model_dump_json())
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
        try:
            r = await self.sender(token, "getUpdates", {"limit": 20})
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:200]}
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
