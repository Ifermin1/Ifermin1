"""Web Push: avisos al teléfono con la consola cerrada y sin Telegram.

El teléfono se suscribe desde la consola (Riesgo → Avisos al teléfono → "Avisar en este teléfono"); el navegador entrega la
notificación aunque la app esté cerrada (Android/Chrome, PC; en iPhone solo instalada en la pantalla de inicio, iOS 16.4+).
Las suscripciones y las claves VAPID viven en la base de datos del engine. Se mandan los mismos eventos que están marcados
para Telegram (la selección es común). Las suscripciones caducadas (404/410) se eliminan solas.
"""
import asyncio
import base64
import json
import time
from typing import Callable

from loguru import logger

from tradepilot.core.events import TOPIC_AUDIT, EventBus
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.services.notify_service import DEFAULT_EVENTS, TITLES, NotifyService

MAX_SUBS = 20


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


class PushService:
    def __init__(self, store: SQLiteStore | None, bus: EventBus, notify: NotifyService | None = None, subject: str = "mailto:tradepilot@example.com") -> None:
        self.store, self.bus, self.notify, self.subject = store, bus, notify, subject or "mailto:tradepilot@example.com"
        raw = store.get_kv("push.subs") if store else None
        self.subs: list[dict] = json.loads(raw) if raw else []
        self.private_key, self.public_key = self._load_keys()
        self.sender: Callable[[dict, str], int] = self._webpush     # (suscripción, payload json) -> HTTP status; lanza en error. Inyectable.
        self.dedupe_s = 60.0
        self._recent: dict[tuple, float] = {}
        self.stats = {"sent": 0, "errors": 0, "removed": 0, "last_error": None, "last_sent_at": None}
        bus.subscribe(TOPIC_AUDIT, self._on_audit)

    # ---- claves VAPID (se generan una vez y se guardan) ----
    def _load_keys(self) -> tuple[str, str]:
        raw = self.store.get_kv("push.vapid") if self.store else None
        if raw:
            d = json.loads(raw)
            return d["private"], d["public"]
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            logger.warning("Web Push: falta la librería 'cryptography' (ejecuta scripts/update.ps1); los avisos push quedan desactivados")
            return "", ""
        key = ec.generate_private_key(ec.SECP256R1())
        priv = _b64url(key.private_numbers().private_value.to_bytes(32, "big"))
        pub = _b64url(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        if self.store:
            self.store.set_kv("push.vapid", json.dumps({"private": priv, "public": pub}))
        return priv, pub

    @property
    def available(self) -> bool:
        if not self.public_key:
            return False
        try:
            import pywebpush  # noqa: F401
            return True
        except ImportError:
            return False

    # ---- suscripciones ----
    def _save(self) -> None:
        if self.store:
            self.store.set_kv("push.subs", json.dumps(self.subs))

    def subscribe(self, subscription: dict, ua: str = "") -> dict:
        ep = str(subscription.get("endpoint") or "")
        keys = subscription.get("keys") or {}
        if not ep.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
            raise ValueError("suscripción incompleta (endpoint https y claves p256dh/auth)")
        self.subs = [s for s in self.subs if s["endpoint"] != ep]
        self.subs.append({"endpoint": ep, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}, "ua": ua[:160], "created_at": time.time()})
        self.subs = self.subs[-MAX_SUBS:]
        self._save()
        return self.state()

    def unsubscribe(self, endpoint: str) -> dict:
        n = len(self.subs)
        self.subs = [s for s in self.subs if s["endpoint"] != endpoint]
        if len(self.subs) != n:
            self._save()
        return self.state()

    def state(self) -> dict:
        return {"available": self.available, "public_key": self.public_key, "subscriptions": len(self.subs),
                "devices": [{"endpoint": s["endpoint"][-24:], "ua": s.get("ua", ""), "created_at": s.get("created_at")} for s in self.subs], "stats": self.stats}

    # ---- entrada: auditoría ----
    def _events(self) -> list[str]:
        return self.notify.config.events if self.notify is not None else list(DEFAULT_EVENTS)

    async def _on_audit(self, event: dict) -> None:
        if not self.subs or not self.available:
            return
        etype = str(event.get("event_type", ""))
        if etype not in self._events():
            return
        target = event.get("target_account") or event.get("source_account") or ""
        key = (etype, target, str(event.get("message", ""))[:80])
        now = time.monotonic()
        if now - self._recent.get(key, -1e9) < self.dedupe_s:
            return
        self._recent[key] = now
        icon, title = TITLES.get(etype, ("•", etype.replace("_", " ").capitalize()))
        await self.broadcast(f"{icon} {title}" + (f" · {target}" if target else ""), str(event.get("message", "")), tag=f"{etype}:{target}")

    # ---- salida ----
    async def broadcast(self, title: str, body: str, tag: str = "", url: str = "/#pocket") -> dict:
        payload = json.dumps({"title": title[:80], "body": body[:400], "tag": tag[:60], "url": url, "at": time.time()}, ensure_ascii=False)
        results = await asyncio.gather(*(asyncio.to_thread(self._send_one, s, payload) for s in list(self.subs)), return_exceptions=True)
        ok = sum(1 for r in results if r is True)
        gone = [s for s, r in zip(list(self.subs), results) if r == "gone"]
        if gone:
            self.subs = [s for s in self.subs if s not in gone]
            self.stats["removed"] += len(gone)
            self._save()
        return {"ok": ok > 0, "sent": ok, "removed": len(gone), "error": self.stats["last_error"] if ok == 0 else None}

    def _send_one(self, sub: dict, payload: str):
        try:
            status = int(self.sender(sub, payload))
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                logger.info(f"Web Push: suscripción caducada, se elimina ({sub['endpoint'][-24:]})")
                return "gone"
            self.stats["errors"] += 1
            self.stats["last_error"] = str(exc)[:200]
            logger.warning(f"Web Push: no se pudo enviar: {exc}")
            return False
        if status in (404, 410):
            return "gone"
        self.stats["sent"] += 1
        self.stats["last_sent_at"] = time.time()
        self.stats["last_error"] = None
        return True

    def _webpush(self, sub: dict, payload: str) -> int:
        from pywebpush import webpush
        r = webpush(subscription_info=sub, data=payload, vapid_private_key=self.private_key, vapid_claims={"sub": self.subject}, ttl=120, timeout=8)
        return int(getattr(r, "status_code", 201))

    async def test(self) -> dict:
        if not self.available:
            return {"ok": False, "sent": 0, "removed": 0, "error": "Web Push no disponible en el engine (ejecuta scripts/update.ps1)"}
        if not self.subs:
            return {"ok": False, "sent": 0, "removed": 0, "error": "ningún teléfono suscrito"}
        return await self.broadcast("✅ TradePilot X", "Los avisos push funcionan en este dispositivo: recibirás límites, cierres, rechazos y caídas del addon.", tag="test")
