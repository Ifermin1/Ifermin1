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
                enabled=bool(row["enabled"]), enabled_source=row["enabled_source"] or "auto",
                alias=row["alias"] or "", reported=False,
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
        new_hidden = 0
        for info in data:
            # Política "auto": una cuenta está activa mientras esté conectada. Si el addon no informa
            # del estado (versión antigua) todo lo reportado cuenta como conectado.
            auto_enabled = True if info.connected is None else info.connected
            snap = self.accounts.get(info.account_id)
            if snap is None:
                snap = self.accounts[info.account_id] = AccountSnapshot(account_id=info.account_id, updated_at=now,
                                                                        enabled=auto_enabled)
                if auto_enabled:
                    logger.info(f"Cuenta conectada detectada: {info.account_id} ({info.connection or 'sin conexión'})")
                else:
                    new_hidden += 1
            elif snap.enabled_source == "auto" and snap.enabled != auto_enabled:
                snap.enabled = auto_enabled
                logger.info(f"Cuenta {info.account_id} {'activada (conectada)' if auto_enabled else 'oculta (desconectada)'}")
            snap.balance = snap.net_liquidity = info.balance
            snap.connected, snap.connection, snap.reported, snap.updated_at = info.connected, info.connection, True, now
            if self.store:
                self.store.upsert_account_seen(info.account_id, info.balance, now.isoformat(), snap.enabled, info.connected)
        if new_hidden:
            logger.info(f"{new_hidden} cuentas nuevas sin conexión quedan ocultas (se activan solas al conectarse)")
        if data:
            for acc, snap in self.accounts.items():
                if acc not in reported:
                    snap.reported = False
                    snap.connected = False if snap.connected is not None else None
                    if snap.enabled_source == "auto" and snap.enabled:
                        snap.enabled = False
                        if self.store:
                            self.store.set_account_settings(acc, enabled=False)
            await self.publish_accounts()
        await self.bus.publish(TOPIC_HEALTH, self.health().model_dump(mode="json"))

    _last_signature: tuple | None = None

    async def publish_accounts(self, force: bool = False) -> None:
        """Publica el snapshot solo si cambió algo relevante (con cientos de cuentas importa)."""
        sig = tuple((a.account_id, a.balance, a.connected, a.enabled, a.alias, a.reported,
                     tuple((p.symbol, p.quantity) for p in a.open_positions)) for a in self.accounts.values())
        if force or sig != self._last_signature:
            self._last_signature = sig
            await self.bus.publish(TOPIC_ACCOUNTS, [a.model_dump(mode="json") for a in self.accounts.values()])

    # ---- gestión desde la consola ----
    def is_enabled(self, account_id: str) -> bool:
        snap = self.accounts.get(account_id)
        return True if snap is None else snap.enabled

    async def set_settings(self, account_id: str, enabled: bool | None = None, alias: str | None = None,
                           auto: bool = False) -> AccountSnapshot:
        """enabled fija la cuenta a mano (source=user); auto=True vuelve a la política automática."""
        snap = self.accounts.get(account_id)
        if snap is None:
            snap = self.accounts[account_id] = AccountSnapshot(account_id=account_id, reported=False, updated_at=datetime.now())
        source = None
        if auto:
            snap.enabled_source = source = "auto"
            snap.enabled = enabled = bool(snap.connected) if snap.connected is not None else snap.reported
        elif enabled is not None:
            snap.enabled = enabled
            snap.enabled_source = source = "user"
        if alias is not None:
            snap.alias = alias.strip()
        if self.store:
            self.store.set_account_settings(account_id, enabled, alias, source)
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
