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
from tradepilot.domain.symbols import tick_size
from tradepilot.infrastructure.brokers.base import BrokerBridge

SYMBOLS = ["NQ 12-26", "ES 12-26", "MNQ 12-26", "CL 11-26"]
# Precio de partida por símbolo: el simulador lo mueve como un paseo aleatorio (antes cada operación salía entre 15.000 y
# 21.000 al azar y el P&L por operación del calendario era disparatado)
START_PRICES = {"NQ 12-26": 20000.0, "ES 12-26": 5600.0, "MNQ 12-26": 20000.0, "CL 11-26": 75.0}


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
        # Modo demo: cada copia a mercado devuelve el fill de la seguidora (como el addon) con un deslizamiento de 0-2 ticks
        # y 80-300 ms de "bróker", para que la latencia, el deslizamiento y "Calidad de ejecución" se vean sin NinjaTrader.
        self.ack_fills = False
        self._px: dict[str, float] = dict(START_PRICES)
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
        symbol = overrides.get("symbol") or random.choice(SYMBOLS)
        px = self._px.get(symbol, 20000.0)
        px = round(px * (1 + random.gauss(0, 0.0006)), 2)
        self._px[symbol] = px
        event = {
            "msg_type": "EXECUTION",
            "account": master,
            "action": random.choice(["BUY", "SELL"]),
            "symbol": symbol,
            "quantity": random.choice([1, 2, 3]),
            "price": px,
            "order_type": "MARKET",
            "state": "FILLED",
            "order_id": uuid.uuid4().hex[:10],
            "execution_id": "E" + uuid.uuid4().hex[:8],
            "timestamp": datetime.now().astimezone().isoformat(timespec="microseconds"),
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
            if self.ack_fills:
                asyncio.get_running_loop().create_task(self._ack_fill(target_account, action, symbol, quantity, price, master_order_id))
        logger.info(f"[mock] orden -> {target_account} {action} {quantity} {symbol}")
        return reply

    async def _ack_fill(self, account: str, action: str, symbol: str, quantity: int, ref_price: float, master_order_id: str) -> None:
        """Fill de la seguidora un instante después, como lo publicaría el addon (EXECUTION con master_order_id)."""
        delay = random.uniform(0.08, 0.30)
        await asyncio.sleep(delay)
        if not self._running:
            return
        ticks = random.choice([0, 0, 0, 0, 1, 1, 2]) * tick_size(symbol)
        price = round((ref_price or 20000.0) + (ticks if action.upper().startswith("BUY") else -ticks), 6)
        await self.bus.publish(TOPIC_MASTER_EVENT, {
            "msg_type": "EXECUTION", "account": account, "action": action, "symbol": symbol, "quantity": quantity,
            "price": price, "order_type": "MARKET", "state": "Filled", "order_id": "F" + uuid.uuid4().hex[:8],
            "master_order_id": master_order_id, "execution_id": "E" + uuid.uuid4().hex[:8],
            "timestamp": datetime.now().astimezone().isoformat(timespec="microseconds"),
        })
