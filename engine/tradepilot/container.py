"""Ensamblado de dependencias del engine (antes `bootstrap.py`)."""
from dataclasses import dataclass

from loguru import logger

from tradepilot.core.config import Settings, settings as default_settings
from tradepilot.core.events import EventBus
from tradepilot.core.netinfo import console_urls
from tradepilot.infrastructure.brokers.base import BrokerBridge
from tradepilot.infrastructure.persistence.journal import Journal
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.services.account_service import AccountService
from tradepilot.services.audit_service import AuditService
from tradepilot.services.replication_service import ReplicationService
from tradepilot.services.risk_service import RiskService
from tradepilot.services.commission_service import CommissionService
from tradepilot.services.performance_service import PerformanceService
from tradepilot.services.sync_service import SyncService


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
    sync: SyncService
    journal: Journal
    commissions: CommissionService | None = None
    performance: PerformanceService | None = None

    async def start(self) -> None:
        await self.bridge.start()
        await self.replication.start()
        await self.accounts.start()
        self.audit.log("ENGINE_START", f"Engine iniciado en modo {self.settings.ENGINE_MODE}")
        logger.info(f"TradePilot X engine listo (modo={self.settings.ENGINE_MODE})")
        if self.settings.API_HOST in ("0.0.0.0", "::"):
            urls = console_urls(self.settings.API_PORT)
            logger.info("Consola disponible en: " + "  |  ".join(urls))
            if len(urls) > 1:
                logger.info(f"Desde el teléfono (misma Wi-Fi) abre {urls[1]} y usa el API_TOKEN del .env")
        if self.settings.API_TOKEN == "cambiame":
            logger.warning("API_TOKEN sigue siendo el valor por defecto; cámbialo en engine/.env antes de exponer el engine")

    async def stop(self) -> None:
        await self.accounts.stop()
        await self.bridge.stop()
        self.journal.close()
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
            bridge.ack_fills = True     # demo: las seguidoras devuelven fills con deslizamiento y latencia simulados
    audit = AuditService(store, bus)
    try:
        from datetime import datetime, timedelta
        store.prune_pnl_samples((datetime.now() - timedelta(days=3)).isoformat(timespec="seconds"))
    except Exception:
        pass
    accounts = AccountService(bridge, bus, store, interval=cfg.ACCOUNT_SYNC_SECONDS)
    risk = RiskService(store, bus, audit, bridge, accounts)
    journal = Journal(cfg.JOURNAL_DIR or None)
    replication = ReplicationService(bridge, store, audit, risk, bus, accounts, journal, cfg.CLOSE_ON_STOP_REJECT)
    replication.audit_lifecycle = cfg.AUDIT_ORDER_LIFECYCLE
    sync = SyncService(accounts, bridge, audit, bus, cfg.DESYNC_GRACE_SECONDS, auto_fix=cfg.AUTO_FIX_OVERCLOSE)
    sync.rules_provider = lambda: replication.rules
    sync.replication = replication
    risk.rules_provider = lambda: replication.rules
    risk.replication = replication
    replication.sync = sync
    replication.on_restart = risk.on_addon_restart
    accounts.on_positions_refreshed = replication.note_positions_refreshed
    accounts.audit = audit
    accounts.limits_provider = lambda: risk.limits
    accounts.eod_time = cfg.DRAWDOWN_EOD_TIME
    commissions = CommissionService(store, cfg.DRAWDOWN_EOD_TIME)
    accounts.commissions = commissions
    replication.commissions = commissions
    risk.commissions = commissions
    performance = PerformanceService(store, commissions, cfg.DRAWDOWN_EOD_TIME)
    accounts.performance = performance
    replication.performance = performance
    if cfg.ENGINE_MODE != "ninja" and cfg.MOCK_DEMO_HISTORY and cfg.DB_PATH != ":memory:":
        try:
            performance.seed_demo(list(getattr(bridge, "accounts", {}).keys()) or ["Sim101", "Sim102"])
        except Exception as exc:
            logger.warning(f"No se pudo generar el histórico de demostración: {exc}")
    async def _after_sync() -> None:
        # independientes: un fallo en una vigilancia no debe apagar la otra
        for name, fn in (("sincronización", sync.check), ("riesgo", risk.check)):
            try:
                await fn()
            except Exception as exc:
                logger.exception(f"Error en vigilancia de {name}: {exc}")
    accounts.after_sync = _after_sync
    return Container(cfg, bus, store, bridge, audit, risk, accounts, replication, sync, journal, commissions, performance)
