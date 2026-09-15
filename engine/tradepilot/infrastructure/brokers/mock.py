"""Simulador de bróker para desarrollo, demos y pruebas.

Genera cuentas ficticias y, si `emit_events=True`, dispara operaciones del
maestro cada pocos segundos para ver el replicador trabajando sin NinjaTrader.
"""
import asyncio
import random
import uuid
from datetime import datetime

from loguru import logger

from tradepilot.core.events import TOPIC_MASTER_EVENT, EventBus
from tradepilot.infrastructure.brokers.base import BrokerBridge

SYMBOLS = ["NQ 12-26", "ES 12-26", "MNQ 12-26", "CL 11-26"]


class MockBridge(BrokerBridge):
    def __init__(self, bus: EventBus, emit_events: bool = True, interval: float = 6.0,
                 accounts: dict[str, float] | None = None) -> None:
        super().__init__(bus)
        self.health.mode = "mock"
        self.emit_events = emit_events
        self.interval = interval
        self.accounts = accounts or {"Sim101": 50_000.0, "Sim102": 25_000.0, "Sim103": 100_000.0}
        self.sent_orders: list[dict] = []
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self.health.connected = True
        self.health.master_feed_up = self.health.follower_feed_up = self.health.sync_up = True
        if self.emit_events:
            self._task = asyncio.create_task(self._emit_loop())
        logger.info("MockBridge online (simulador)")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self.health.connected = False

    async def _emit_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval)
                await self.emit_master_event()
            except asyncio.CancelledError:
                break

    async def emit_master_event(self, **overrides) -> dict:
        master = next(iter(self.accounts))
        event = {
            "msg_type": "EXECUTION",
            "account": master,
            "action": random.choice(["BUY", "SELL"]),
            "symbol": random.choice(SYMBOLS),
            "quantity": random.choice([1, 2, 3]),
            "price": round(random.uniform(15_000, 21_000), 2),
            "order_type": "MARKET",
            "state": "FILLED",
            "order_id": uuid.uuid4().hex[:10],
        }
        event.update(overrides)
        self.health.last_msg_in = datetime.now()
        await self.bus.publish(TOPIC_MASTER_EVENT, event)
        return event

    async def get_accounts(self) -> dict[str, float]:
        self.health.last_sync = datetime.now()
        # pequeño ruido para que el dashboard se mueva
        return {k: round(v + random.uniform(-25, 25), 2) for k, v in self.accounts.items()}

    async def send_order(self, target_account, action, symbol, quantity, order_type, master_order_id,
                         msg_type="EXECUTION", price=0.0, limit_price=0.0, stop_price=0.0) -> None:
        self.sent_orders.append({
            "account": target_account, "action": action, "symbol": symbol, "quantity": quantity,
            "order_type": order_type, "master_order_id": master_order_id, "msg_type": msg_type, "price": price,
            "limit_price": limit_price, "stop_price": stop_price,
        })
        self.health.last_msg_out = datetime.now()
        logger.info(f"[mock] orden -> {target_account} {action} {quantity} {symbol}")
