"""Bus de eventos en proceso.

Sustituye al antiguo EventBus sobre ZMQ loopback: el proceso ya no necesita
sockets para hablar consigo mismo, y evitamos el choque de puertos (el bus
antiguo hacía bind en 5555, el mismo puerto donde NinjaTrader publica).
"""
import asyncio
import inspect
from collections import defaultdict
from typing import Any, Awaitable, Callable

from loguru import logger

Handler = Callable[[Any], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        if handler in self._handlers[topic]:
            self._handlers[topic].remove(handler)

    async def publish(self, topic: str, payload: Any = None) -> None:
        calls = [(h, payload) for h in list(self._handlers.get(topic, []))]
        calls += [(h, {"topic": topic, "data": payload}) for h in list(self._handlers.get("*", []))]
        for handler, arg in calls:
            try:
                result = handler(arg)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # un handler roto no debe tumbar al resto
                logger.exception(f"Error en handler de '{topic}': {exc}")

    def publish_nowait(self, topic: str, payload: Any = None) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(topic, payload))


# Tópicos usados en el motor
TOPIC_MASTER_EVENT = "broker.master_event"   # evento crudo del maestro
TOPIC_ACCOUNTS = "accounts.snapshot"         # lista de cuentas actualizada
TOPIC_AUDIT = "audit.event"                  # nueva entrada de auditoría
TOPIC_HEALTH = "broker.health"               # salud del puente
TOPIC_RISK = "risk.state"                    # cambio de kill switch / límites
TOPIC_PRICE = "market.price"                 # tick de precio del addon
