import os
from pathlib import Path
from typing import Dict, Any
import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    
    # App
    APP_NAME: str = "TradePilot X"
    APP_ENV: str = "development"
    DEBUG: bool = True
    
    # ZMQ Infrastructure
    ZMQ_HOST: str = "127.0.0.1"
    ZMQ_MASTER_PORT: int = 5555  # Master PUB -> we SUB
    ZMQ_FOLLOWER_PORT: int = 5556 # Follower SUB -> we PUB
    ZMQ_SYNC_PORT: int = 5557    # Sync REP -> we REQ
    
    # Fallback to older properties if used elsewhere
    ZMQ_PUB_PORT: int = 5555 
    
    # AI
    GEMINI_API_KEY: str = ""

    @property
    def zmq_pub_url(self) -> str:
        return f"tcp://{self.ZMQ_HOST}:{self.ZMQ_PUB_PORT}"

settings = AppSettings()
