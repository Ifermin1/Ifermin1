import asyncio
from datetime import datetime

from loguru import logger

from tradepilot.core.events import TOPIC_ACCOUNTS, TOPIC_HEALTH, EventBus
from tradepilot.domain.accounts import AccountSnapshot, BridgeHealth, PositionSnapshot
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.infrastructure.brokers.base import BrokerBridge


class AccountService:
    """Sincroniza periódicamente las cuentas del bróker y publica snapshots."""

    def __init__(self, bridge: BrokerBridge, bus: EventBus, store: SQLiteStore | None = None,
                 interval: float = 2.0) -> None:
        self.bridge = bridge
        self.bus = bus
        self.store = store
        self.interval = interval
        self.accounts: dict[str, AccountSnapshot] = {}
        # Cuentas recordadas de sesiones anteriores: aparecen aunque el bróker aún no las reporte
        for row in (store.get_accounts() if store else []):
            self.accounts[row["account_id"]] = AccountSnapshot(
                account_id=row["account_id"], balance=row["last_balance"] or 0.0, net_liquidity=row["last_balance"] or 0.0,
                enabled=bool(row["enabled"]), alias=row["alias"] or "", reported=False,
                updated_at=datetime.fromisoformat(row["last_seen"]) if row["last_seen"] else datetime.now())
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
        reported = {a.account_id for a in data}
        for info in data:
            snap = self.accounts.get(info.account_id)
            if snap is None:
                snap = self.accounts[info.account_id] = AccountSnapshot(account_id=info.account_id, updated_at=now)
                logger.info(f"Cuenta nueva detectada: {info.account_id} ({info.connection or 'sin conexión'})")
            snap.balance = snap.net_liquidity = info.balance
            snap.connected, snap.connection, snap.reported, snap.updated_at = info.connected, info.connection, True, now
            if self.store:
                self.store.upsert_account_seen(info.account_id, info.balance, now.isoformat())
        if data:
            for acc, snap in self.accounts.items():
                if acc not in reported:
                    snap.reported = False
                    snap.connected = False if snap.connected is not None else None
            await self.publish_accounts()
        await self.bus.publish(TOPIC_HEALTH, self.health().model_dump(mode="json"))

    async def publish_accounts(self) -> None:
        await self.bus.publish(TOPIC_ACCOUNTS, [a.model_dump(mode="json") for a in self.accounts.values()])

    # ---- gestión desde la consola ----
    def is_enabled(self, account_id: str) -> bool:
        snap = self.accounts.get(account_id)
        return True if snap is None else snap.enabled

    async def set_settings(self, account_id: str, enabled: bool | None = None, alias: str | None = None) -> AccountSnapshot:
        snap = self.accounts.get(account_id)
        if snap is None:
            snap = self.accounts[account_id] = AccountSnapshot(account_id=account_id, reported=False, updated_at=datetime.now())
        if enabled is not None:
            snap.enabled = enabled
        if alias is not None:
            snap.alias = alias.strip()
        if self.store:
            self.store.set_account_settings(account_id, enabled, alias)
        await self.publish_accounts()
        return snap

    async def forget(self, account_id: str) -> bool:
        """Olvida una cuenta que el bróker ya no reporta."""
        snap = self.accounts.get(account_id)
        if snap is None or snap.reported:
            return False
        del self.accounts[account_id]
        if self.store:
            self.store.delete_account(account_id)
        await self.publish_accounts()
        return True

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
