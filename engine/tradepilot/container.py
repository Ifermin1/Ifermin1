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
from tradepilot.services.notify_service import NotifyService
from tradepilot.services.news_service import NewsService
from tradepilot.services.ai_service import AIService
from tradepilot.services.trade_service import TradeService
from tradepilot.services.market_service import MarketService
from tradepilot.services.trail_service import TrailService
from tradepilot.services.auto_risk_service import AutoRiskService
from tradepilot.services.advisor_service import AdvisorService
from tradepilot.services.coach_service import CoachService
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
    notify: NotifyService | None = None
    news: NewsService | None = None
    trade: TradeService | None = None
    market: MarketService | None = None
    trail: TrailService | None = None
    auto_risk: AutoRiskService | None = None
    advisor: AdvisorService | None = None
    coach: CoachService | None = None

    async def start(self) -> None:
        if self.advisor is not None:
            from tradepilot.api.routes import _presets
            self.advisor.presets_provider = lambda: _presets(self)
        await self.bridge.start()
        await self.replication.start()
        await self.accounts.start()
        if self.news is not None:
            await self.news.start()
        if self.trail is not None:
            await self.trail.start()
        self.audit.log("ENGINE_START", f"Engine iniciado en modo {self.settings.ENGINE_MODE}")
        if self.news is not None and not self.news.ai.enabled:
            logger.info("Calendario: sin ANTHROPIC_API_KEY, los avisos de noticias llevan un análisis por reglas (pon la clave en engine/.env para usar la IA)")
        self.risk.warn_if_persisted()
        from tradepilot.api.routes import public_url, weak_token
        if weak_token(self.settings.API_TOKEN):
            msg = "API_TOKEN es débil (corto o el de ejemplo). Cámbialo en engine/.env por uno largo y aleatorio antes de publicar la consola."
            logger.warning(msg)
            if public_url(self):
                self.audit.log("WEAK_TOKEN", "La consola está PUBLICADA en Internet con un token débil: " + msg)
        if (url := public_url(self)):
            logger.info(f"Consola pública (túnel): {url}")
        logger.info(f"TradePilot X engine listo (modo={self.settings.ENGINE_MODE})")
        if self.settings.API_HOST in ("0.0.0.0", "::"):
            urls = console_urls(self.settings.API_PORT)
            logger.info("Consola disponible en: " + "  |  ".join(urls))
            if len(urls) > 1:
                logger.info(f"Desde el teléfono (misma Wi-Fi) abre {urls[1]} y usa el API_TOKEN del .env")
        if self.settings.API_TOKEN == "cambiame":
            logger.warning("API_TOKEN sigue siendo el valor por defecto; cámbialo en engine/.env antes de exponer el engine")

    async def stop(self) -> None:
        if self.trail is not None:
            await self.trail.stop()
        if self.news is not None:
            await self.news.stop()
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
    notify = NotifyService(store, bus)
    ai = AIService(cfg.ANTHROPIC_API_KEY, cfg.AI_MODEL)
    news = NewsService(ttl_s=max(60, cfg.NEWS_REFRESH_SECONDS), store=store, ai=ai, audit=audit, bus=bus,
                       alert_minutes=cfg.NEWS_ALERT_MINUTES, countries=cfg.NEWS_COUNTRIES, min_impact=cfg.NEWS_MIN_IMPACT)
    if cfg.ENGINE_MODE != "ninja" and cfg.MOCK_DEMO_HISTORY:
        news.seed_demo()
    performance = PerformanceService(store, commissions, cfg.DRAWDOWN_EOD_TIME)
    accounts.performance = performance
    replication.performance = performance
    if cfg.ENGINE_MODE != "ninja" and cfg.MOCK_DEMO_HISTORY and cfg.DB_PATH != ":memory:":
        try:
            performance.seed_demo(list(getattr(bridge, "accounts", {}).keys()) or ["Sim101", "Sim102"], end_balances=getattr(bridge, "accounts", None))
        except Exception as exc:
            logger.warning(f"No se pudo generar el histórico de demostración: {exc}")
    trade = TradeService(bridge, accounts, risk, audit, cfg)
    replication.on_manual_status = trade.on_manual_status
    market = MarketService(bus)
    auto_risk = AutoRiskService(risk, accounts, audit, cfg)
    accounts.on_account_seen = auto_risk.on_account_seen
    advisor = AdvisorService(accounts, risk, replication, store)
    coach = CoachService(store, accounts, risk, news, market, ai, trade)
    trail = TrailService(trade, accounts, market, ai, audit, bridge, store, interval_s=cfg.TRAIL_INTERVAL_S, ai_interval_s=cfg.AI_TRAIL_INTERVAL_S, ai_model=cfg.AI_TRAIL_MODEL)
    trade.trail = trail

    async def _after_sync() -> None:
        # independientes: un fallo en una vigilancia no debe apagar la otra
        for name, fn in (("sincronización", sync.check), ("riesgo", risk.check), ("salidas huérfanas", trade.sweep_orphans), ("trailing pendiente", trade.apply_pending_trails)):
            try:
                await fn()
            except Exception as exc:
                logger.exception(f"Error en vigilancia de {name}: {exc}")
    accounts.after_sync = _after_sync
    return Container(cfg, bus, store, bridge, audit, risk, accounts, replication, sync, journal, commissions, performance, notify, news, trade, market, trail, auto_risk, advisor, coach)
