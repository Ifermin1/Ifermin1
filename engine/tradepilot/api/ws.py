import asyncio
import json
from typing import Any

from fastapi import WebSocket
from loguru import logger


class WsHub:
    """Mantiene los WebSockets abiertos y les reenvía los eventos del bus."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    @property
    def count(self) -> int:
        return len(self._clients)

    async def broadcast(self, topic: str, data: Any) -> None:
        if not self._clients:
            return
        msg = json.dumps({"topic": topic, "data": data}, default=str)
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)
        if dead:
            logger.debug(f"WS: {len(dead)} clientes desconectados")
