import asyncio
from datetime import datetime

from loguru import logger

from tradepilot.core.events import TOPIC_ACCOUNTS, TOPIC_HEALTH, EventBus
from tradepilot.domain.accounts import AccountSnapshot, BridgeHealth, PositionSnapshot
from tradepilot.infrastructure.brokers.base import BrokerBridge


class AccountService:
    """Sincroniza periódicamente las cuentas del bróker y publica snapshots."""

    def __init__(self, bridge: BrokerBridge, bus: EventBus, interval: float = 2.0) -> None:
        self.bridge = bridge
        self.bus = bus
        self.interval = interval
        self.accounts: dict[str, AccountSnapshot] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def sync_once(self) -> None:
        data = await self.bridge.get_accounts()
        now = datetime.now()
        for acc, balance in data.items():
            snap = self.accounts.get(acc)
            if snap is None:
                self.accounts[acc] = AccountSnapshot(account_id=acc, balance=balance, net_liquidity=balance, updated_at=now)
            else:
                snap.balance = balance
                snap.net_liquidity = balance
                snap.updated_at = now
        if data:
            await self.bus.publish(TOPIC_ACCOUNTS, [a.model_dump(mode="json") for a in self.accounts.values()])
        await self.bus.publish(TOPIC_HEALTH, self.health().model_dump(mode="json"))

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error sincronizando cuentas: {exc}")
            await asyncio.sleep(self.interval)

    async def update_position(self, account_id: str, symbol: str, market_position: str, quantity: int,
                              avg_price: float) -> None:
        """POSITION del addon: mantiene las posiciones abiertas de la cuenta."""
        now = datetime.now()
        snap = self.accounts.get(account_id)
        if snap is None:
            snap = self.accounts[account_id] = AccountSnapshot(account_id=account_id, updated_at=now)
        positions = [p for p in snap.open_positions if p.symbol != symbol]
        if quantity > 0 and market_position.upper() != "FLAT":
            signed = quantity if market_position.upper() == "LONG" else -quantity
            positions.append(PositionSnapshot(account_id=account_id, symbol=symbol, quantity=signed, avg_price=avg_price))
        snap.open_positions = positions
        snap.updated_at = now
        await self.bus.publish(TOPIC_ACCOUNTS, [a.model_dump(mode="json") for a in self.accounts.values()])

    def all(self) -> list[AccountSnapshot]:
        return list(self.accounts.values())

    def health(self) -> BridgeHealth:
        return self.bridge.health
