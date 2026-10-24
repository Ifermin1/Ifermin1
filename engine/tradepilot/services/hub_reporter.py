"""Reporter del hub: manda a un servidor central (hub/) lo que pasa en este engine.

Cada HUB_INTERVAL_S segundos encola las operaciones nuevas, una foto del estado (cuentas, P&L del día, posiciones, kill
switch, conexión) y, según llegan, los avisos importantes de la auditoría; después intenta vaciar la cola con un POST a
`HUB_URL/api/ingest`. Si el hub no responde, la cola (tabla `hub_outbox`, en el SQLite del engine) se conserva y se
reintenta en el siguiente ciclo; solo se descartan los elementos más antiguos si pasan de MAX_QUEUE. Nunca bloquea la
copia: va en su propia tarea y con tiempos de espera cortos.
"""
import asyncio
import json
import socket
import time
from datetime import datetime
from typing import Awaitable, Callable

from loguru import logger

from tradepilot.core.events import TOPIC_AUDIT, EventBus
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.services.notify_service import DEFAULT_EVENTS

MAX_QUEUE = 5000
BATCH = 200
EVENT_TYPES = set(DEFAULT_EVENTS) | {"MANUAL_ORDER", "MANUAL_FILL", "TRAIL_START", "TRAIL_END", "PRESET_APPLIED", "DAY_REOPENED"}


class HubReporter:
    def __init__(self, store: SQLiteStore, bus: EventBus, url: str, token: str, node: str = "", interval_s: int = 30,
                 accounts=None, risk=None, bridge=None, mode: str = "", version: str = "") -> None:
        self.store, self.bus = store, bus
        self.url, self.token = url.strip().rstrip("/"), token.strip()
        self.node = node.strip() or socket.gethostname()
        self.interval_s = max(5, int(interval_s))
        self.accounts, self.risk, self.bridge, self.mode, self.version = accounts, risk, bridge, mode, version
        self.sender: Callable[[str, str, dict], Awaitable[dict]] = self._http    # (url, token, body) -> respuesta json; inyectable
        self.stats = {"sent": 0, "batches": 0, "errors": 0, "last_error": None, "last_ok_at": None, "dropped": 0}
        self._last_trade_id = int(store.get_kv("hub.last_trade_id") or 0)
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        if self.enabled:
            bus.subscribe(TOPIC_AUDIT, self._on_audit)

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)

    # ---- ciclo de vida ----
    async def start(self) -> None:
        if not self.enabled:
            return
        self._task = asyncio.get_running_loop().create_task(self._loop())
        logger.info(f"Hub: reportando a {self.url} como '{self.node}' cada {self.interval_s} s")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(2)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Hub: error en el ciclo: {exc}")
            await asyncio.sleep(self.interval_s)

    async def tick(self) -> dict:
        self.collect()
        return await self.flush()

    # ---- recogida ----
    def _enqueue(self, kind: str, data: dict, ref: int | str | None = None) -> None:
        item = {"kind": kind, "ref": ref, "at": datetime.now().astimezone().isoformat(timespec="seconds"), "data": data}
        self.store.outbox_add(kind, json.dumps(item, ensure_ascii=False, default=str), item["at"])

    async def _on_audit(self, event: dict) -> None:
        if str(event.get("event_type", "")) in EVENT_TYPES:
            self._enqueue("event", {"event_type": event.get("event_type"), "account": event.get("target_account") or event.get("source_account"),
                                    "message": str(event.get("message", ""))[:400], "timestamp": event.get("timestamp")}, ref=event.get("id"))

    def collect(self) -> int:
        """Operaciones nuevas desde la última enviada y una foto del estado (solo se guarda la más reciente)."""
        n = 0
        for t in self.store.get_trades_since(self._last_trade_id, BATCH):
            self._enqueue("trade", t, ref=t["id"])
            self._last_trade_id = max(self._last_trade_id, int(t["id"]))
            n += 1
        if n:
            self.store.set_kv("hub.last_trade_id", str(self._last_trade_id))
        self.store.outbox_delete_kind("snapshot")
        self._enqueue("snapshot", self.snapshot())
        dropped = self.store.outbox_trim(MAX_QUEUE)
        if dropped:
            self.stats["dropped"] += dropped
        return n

    def snapshot(self) -> dict:
        accs = []
        if self.accounts is not None:
            for a in self.accounts.accounts.values():
                accs.append({"account_id": a.account_id, "alias": a.alias, "enabled": a.enabled, "firm": a.firm, "plan": a.plan, "tags": list(a.tags),
                             "balance": a.balance, "net_pnl": a.net_pnl, "daily_pnl": a.daily_pnl, "contracts_today": a.contracts_today,
                             "positions": [{"symbol": p.symbol, "quantity": p.quantity, "avg_price": p.avg_price, "unrealized_pnl": p.unrealized_pnl} for p in a.open_positions if p.quantity],
                             "drawdown": a.drawdown.drawdown, "drawdown_limit": a.drawdown.limit, "desync": a.desync, "connected": a.connected})
        rs = self.risk.state() if self.risk is not None else None
        h = self.bridge.health if self.bridge is not None else None
        return {"node": self.node, "mode": self.mode, "version": self.version, "accounts": accs,
                "day_total": self.risk.day_total() if self.risk is not None else 0.0,
                "kill_switch": bool(rs.kill_switch) if rs else False, "kill_switch_reason": rs.kill_switch_reason if rs else None, "day_closed": bool(rs.day_closed) if rs else False,
                "connected": bool(h.connected) if h else False, "master_account": h.master_account if h else None, "addon_version": h.addon_version if h else None}

    # ---- envío ----
    async def flush(self) -> dict:
        if not self.enabled:
            return {"ok": False, "sent": 0, "error": "hub no configurado"}
        async with self._lock:
            rows = self.store.outbox_peek(BATCH)
            if not rows:
                return {"ok": True, "sent": 0, "error": None}
            items = []
            for r in rows:
                try:
                    items.append(json.loads(r["payload"]))
                except ValueError:
                    continue
            body = {"node": {"id": self.node, "mode": self.mode, "version": self.version}, "items": items}
            try:
                resp = await self.sender(self.url + "/api/ingest", self.token, body)
                if not resp.get("ok"):
                    raise RuntimeError(str(resp.get("error") or resp)[:200])
            except Exception as exc:
                self.stats["errors"] += 1
                self.stats["last_error"] = str(exc)[:200]
                self.store.outbox_fail([r["id"] for r in rows], str(exc))
                logger.warning(f"Hub: no se pudo enviar ({len(rows)} elementos en cola): {exc}")
                return {"ok": False, "sent": 0, "error": self.stats["last_error"]}
            self.store.outbox_delete([r["id"] for r in rows])
            self.stats["sent"] += len(rows)
            self.stats["batches"] += 1
            self.stats["last_ok_at"] = time.time()
            self.stats["last_error"] = None
            return {"ok": True, "sent": len(rows), "error": None}

    def state(self) -> dict:
        return {"enabled": self.enabled, "url": self.url, "node": self.node, "interval_s": self.interval_s, "queued": self.store.outbox_count(),
                "last_trade_id": self._last_trade_id, "stats": self.stats}

    @staticmethod
    async def _http(url: str, token: str, body: dict) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
            if r.status_code >= 400:
                return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:120]}"}
            try:
                return r.json()
            except ValueError:
                return {"ok": False, "error": f"respuesta no válida (HTTP {r.status_code})"}
