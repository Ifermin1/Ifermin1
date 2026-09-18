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


settings = Settings()
