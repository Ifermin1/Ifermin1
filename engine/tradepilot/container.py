"""Ensamblado de dependencias del engine (antes `bootstrap.py`)."""
from dataclasses import dataclass

from loguru import logger

from tradepilot.core.config import Settings, settings as default_settings
from tradepilot.core.events import EventBus
from tradepilot.infrastructure.brokers.base import BrokerBridge
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.services.account_service import AccountService
from tradepilot.services.audit_service import AuditService
from tradepilot.services.replication_service import ReplicationService
from tradepilot.services.risk_service import RiskService


@dataclass
class Container:
    settings: Settings
    bus: EventBus
    store: SQLiteStore
    bridge: BrokerBridge
    audit: AuditService
    risk: RiskService
    accounts: AccountService
    replication: ReplicationService

    async def start(self) -> None:
        await self.bridge.start()
        await self.replication.start()
        await self.accounts.start()
        self.audit.log("ENGINE_START", f"Engine iniciado en modo {self.settings.ENGINE_MODE}")
        logger.info(f"TradePilot X engine listo (modo={self.settings.ENGINE_MODE})")

    async def stop(self) -> None:
        await self.accounts.stop()
        await self.bridge.stop()
        self.store.close()


def build_container(cfg: Settings | None = None, bridge: BrokerBridge | None = None,
                    bus: EventBus | None = None) -> Container:
    cfg = cfg or default_settings
    bus = bus or (bridge.bus if bridge is not None else EventBus())
    store = SQLiteStore(cfg.DB_PATH)
    if bridge is None:
        if cfg.ENGINE_MODE == "ninja":
            from tradepilot.infrastructure.brokers.ninja_zmq import NinjaZmqBridge
            bridge = NinjaZmqBridge(bus)
        else:
            from tradepilot.infrastructure.brokers.mock import MockBridge
            bridge = MockBridge(bus)
    audit = AuditService(store, bus)
    risk = RiskService(store, bus, audit)
    accounts = AccountService(bridge, bus, interval=cfg.ACCOUNT_SYNC_SECONDS)
    replication = ReplicationService(bridge, store, audit, risk, bus)
    return Container(cfg, bus, store, bridge, audit, risk, accounts, replication)
