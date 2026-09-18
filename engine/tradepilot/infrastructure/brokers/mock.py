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
from tradepilot.domain.accounts import BrokerAccount, BrokerPosition, WorkingOrder
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
        self.disconnected: set[str] = set()   # cuentas que el simulador reporta como desconectadas
        self.positions: dict[tuple[str, str], int] = {}   # (cuenta, símbolo) -> qty con signo
        self.flattened: list[str] = []
        self.fill_orders = True   # las órdenes enviadas actualizan la posición del simulador
        self.pnl: dict[str, float] = {}   # P&L del día por cuenta (pruebas)
        self.unrealized: dict[str, float] = {}   # flotante por cuenta (pruebas)
        self.noise = 25.0                 # ruido del balance para que el dashboard se mueva (0 en pruebas)
        self.sent_orders: list[dict] = []
        self.watched: list[str] = []      # cuentas por las que el engine pidió WATCH
        self.working_orders: list[tuple[str, WorkingOrder]] = []   # (cuenta, orden) que reporta GET_ORDERS (pruebas)
        self.batches: list[int] = []      # tamaño de cada lote recibido por send_orders (pruebas)
        self.boot: str | None = None      # simula el "PONG|boot|seq" del addon >= 1.8 (pruebas)
        self.watch_ok = True              # False: el addon no confirma el WATCH (pruebas)
        self.next_replies: list[str] = []  # respuestas forzadas a las próximas órdenes (pruebas): "IGNORED|...", "OK|EXECUTION_PARTIAL"
        self.seq: int | None = None
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

    async def resubscribe(self) -> None:
        self.resubscribes = getattr(self, "resubscribes", 0) + 1
        self.health.resubscribes += 1

    async def watch(self, account: str) -> bool:
        self.watched.append(account)
        return self.watch_ok

    async def ping_state(self) -> tuple[bool, str | None, int | None]:
        return True, self.boot, self.seq

    async def get_orders(self) -> list[tuple[str, WorkingOrder]] | None:
        return list(self.working_orders)

    async def get_positions(self) -> list[BrokerPosition] | None:
        return [BrokerPosition(account_id=a, symbol=sym, quantity=q) for (a, sym), q in self.positions.items() if q]

    async def flatten(self, account: str) -> str:
        if account not in self.accounts:
            raise RuntimeError(f"cuenta desconocida: {account}")
        n = sum(1 for (a, _), q in self.positions.items() if a == account and q)
        for k in list(self.positions):
            if k[0] == account:
                self.positions[k] = 0
        self.flattened.append(account)
        await self.bus.publish(TOPIC_MASTER_EVENT, {"msg_type": "FLATTENED", "account": account, "instruments_closed": n})
        return f"OK|{account}|{n}"

    async def set_master(self, account: str) -> str:
        if account not in self.accounts:
            raise RuntimeError(f"cuenta desconocida: {account}")
        self.health.master_account = account
        await self.bus.publish(TOPIC_MASTER_EVENT, {"msg_type": "HEARTBEAT", "account": account, "version": "mock"})
        return account

    async def get_accounts(self) -> list[BrokerAccount]:
        self.health.last_sync = datetime.now()
        # pequeño ruido para que el dashboard se mueva
        return [BrokerAccount(account_id=k, balance=round(v + random.uniform(-self.noise, self.noise), 2),
                              connected=k not in self.disconnected, connection="Simulación",
                              realized_pnl=self.pnl.get(k, 0.0), unrealized_pnl=self.unrealized.get(k, 0.0))
                for k, v in self.accounts.items()]

    async def send_orders(self, orders: list[dict]) -> list:
        self.batches.append(len(orders))
        return await super().send_orders(orders)

    async def send_order(self, target_account, action, symbol, quantity, order_type, master_order_id,
                         msg_type="EXECUTION", price=0.0, limit_price=0.0, stop_price=0.0, entry=None,
                         master_filled_scaled=None) -> str | None:
        reply = self.next_replies.pop(0) if self.next_replies else "OK|" + msg_type
        if reply.startswith("IGNORED|"):
            logger.info(f"[mock] orden -> {target_account} {action} {quantity} {symbol} ignorada: {reply[8:]}")
            return reply
        self.sent_orders.append({
            "account": target_account, "action": action, "symbol": symbol, "quantity": quantity,
            "order_type": order_type, "master_order_id": master_order_id, "msg_type": msg_type, "price": price,
            "limit_price": limit_price, "stop_price": stop_price, "master_filled_scaled": master_filled_scaled, **(entry or {}),
        })
        self.health.last_msg_out = datetime.now()
        if self.fill_orders and msg_type == "EXECUTION":
            k = (target_account, symbol)
            sign = 1 if action.upper().startswith("BUY") else -1
            self.positions[k] = self.positions.get(k, 0) + sign * quantity
        logger.info(f"[mock] orden -> {target_account} {action} {quantity} {symbol}")
        return reply
