from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "TradePilot X"
    DEBUG: bool = False

    # "mock": simulador integrado (demo / desarrollo). "ninja": puente ZMQ real.
    ENGINE_MODE: Literal["mock", "ninja"] = "mock"

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_TOKEN: str = "cambiame"
    CORS_ORIGINS: str = "*"
    # URL pública de la consola (túnel). Si está vacía se lee de PUBLIC_URL_FILE, que escribe scripts/publish.ps1.
    PUBLIC_URL: str = ""
    PUBLIC_URL_FILE: str = "data/public_url.txt"
    # Web Push: contacto que va en las claves VAPID (mailto:). Las claves se generan solas la primera vez.
    PUSH_SUBJECT: str = "mailto:tradepilot@example.com"
    # Carpeta con el build de la consola web (se sirve desde el mismo proceso)
    WEB_DIST: str = "../web/dist"

    # ZMQ (NinjaTrader)
    ZMQ_HOST: str = "127.0.0.1"
    ZMQ_MASTER_PORT: int = 5555   # NinjaTrader PUB -> engine SUB
    ZMQ_FOLLOWER_PORT: int = 5556 # engine PUB -> NinjaTrader executor SUB
    ZMQ_SYNC_PORT: int = 5557     # engine REQ -> NinjaTrader REP

    ACCOUNT_SYNC_SECONDS: float = 2.0
    DB_PATH: str = "data/tradepilot.db"
    JOURNAL_DIR: str = "data/journal"     # "" para desactivar

    # Protecciones
    DESYNC_GRACE_SECONDS: float = 6.0     # cuánto puede diferir una seguidora antes de marcar DESYNC
    CLOSE_ON_STOP_REJECT: bool = True     # stop rechazado en una seguidora => cerrar su posición
    AUTO_FIX_OVERCLOSE: bool = True       # seguidora invertida / con posición sin maestra tras una copia => cerrarla sola
    DRAWDOWN_EOD_TIME: str = "17:00"      # hora local (PC del engine) del cierre del día para el drawdown EOD (APEX/Topstep: 17:00 ET)
    AUDIT_ORDER_LIFECYCLE: bool = False   # auditar también los estados intermedios (Initialized, Submitted, Accepted...)
    MIN_ADDON_VERSION: str = "2.6"
    MANUAL_ADDON_VERSION: str = "2.7"      # operar desde la consola (Operar) requiere este addon o superior
    MANUAL_MAX_QTY: int = 20               # contratos máximos por orden manual desde la consola
    MANUAL_CONFIRM_TIMEOUT_S: float = 8.0
    # Riesgo automático: al aparecer una cuenta sin límites se le aplica el perfil del catálogo (firma por el nombre, tamaño por el saldo)
    AUTO_RISK: bool = True
    AUTO_RISK_FIRM: str = "apex"           # firma por defecto si el nombre de la cuenta no la delata
    AUTO_RISK_PLAN: str = "eval"           # plan por defecto (apex: eval | pa | static | pa_static)
    AUTO_RISK_MAX_CONTRACTS: int = 5       # tope de contratos por cuenta (se usa el menor entre este y el del plan); 0 = el del plan
    AUTO_RISK_BUFFER: float = 250.0        # colchón en $ antes del suelo del drawdown al que el engine cierra y pausa
    TRAIL_INTERVAL_S: float = 1.0          # cada cuánto revisa el trailing stop cada posición
    AI_TRAIL_INTERVAL_S: int = 60          # trailing IA: cada cuánto se le pide a Claude dónde poner el stop
    AI_TRAIL_MODEL: str = ""               # modelo para el trailing IA; vacío = AI_MODEL (claude-sonnet-5-5 responde más rápido y barato)  # si NinjaTrader no confirma la orden manual en este tiempo se reporta como NO confirmada
    # Calendario económico y análisis con IA (Claude). Sin ANTHROPIC_API_KEY los avisos llevan un análisis por reglas.
    ANTHROPIC_API_KEY: str = ""
    AI_MODEL: str = "claude-opus-5-5"
    NEWS_REFRESH_SECONDS: int = 300       # cada cuánto se vuelve a descargar el calendario (y se detectan los datos publicados)
    NEWS_ALERT_MINUTES: int = 30          # aviso (Inicio + Telegram) cuando falta menos de esto para una noticia
    NEWS_COUNTRIES: str = "USD"           # países vigilados (USD,EUR,...); "" = todos
    NEWS_MIN_IMPACT: str = "low"          # impacto mínimo vigilado: low | medium | high
    MOCK_DEMO_HISTORY: bool = True          # simulador: genera un mes de operaciones inventadas para el calendario (no en :memory:)


settings = Settings()
