from typing import Optional

from pydantic import BaseModel, Field


class ExecutionOptions(BaseModel):
    target_root: Optional[str] = None                      # "MNQ" para operar el micro
    entry_mode: Optional[str] = Field(default=None, pattern="^(market|limit)$")
    tolerance_ticks: Optional[int] = Field(default=None, ge=0, le=50)
    entry_timeout_s: Optional[int] = Field(default=None, ge=1, le=120)
    entry_fallback: Optional[str] = Field(default=None, pattern="^(market|cancel)$")


class RuleCreate(ExecutionOptions):
    master_account: str = Field(min_length=1)
    follower_account: str = Field(min_length=1)
    multiplier: float = Field(default=1.0, gt=0)
    symbol_filter: Optional[str] = None
    enabled: bool = True


class NotifyRequest(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    events: list[str] = Field(default_factory=list)


class DiscoverChatRequest(BaseModel):
    bot_token: Optional[str] = None


class EntryPreset(BaseModel):
    """Mismo modo de entrada para todas las seguidoras de la maestra (botones de "Calidad de ejecución")."""
    entry_mode: str = Field(pattern="^(market|limit)$")
    tolerance_ticks: Optional[int] = Field(default=None, ge=0, le=50)
    entry_timeout_s: Optional[int] = Field(default=None, ge=1, le=120)
    entry_fallback: Optional[str] = Field(default=None, pattern="^(market|cancel)$")


class RuleUpdate(ExecutionOptions):
    master_account: Optional[str] = None
    follower_account: Optional[str] = None
    multiplier: Optional[float] = Field(default=None, gt=0)
    symbol_filter: Optional[str] = None
    enabled: Optional[bool] = None


class KillSwitchRequest(BaseModel):
    active: bool
    reason: Optional[str] = None
    flatten: bool = False          # además de bloquear, cerrar posiciones
    flatten_master: bool = True    # ... incluida la maestra


class FlattenRequest(BaseModel):
    reason: Optional[str] = None


class FlattenAllRequest(BaseModel):
    include_master: bool = False
    reason: Optional[str] = None


class RiskLimitUpsert(BaseModel):
    account_id: str = Field(min_length=1)
    max_daily_loss: float = Field(default=0.0, ge=0)
    max_daily_profit: float = Field(default=0.0, ge=0)
    max_position_size: int = Field(default=0, ge=0)
    max_trailing_drawdown: float = Field(default=0.0, ge=0)
    drawdown_mode: str = Field(default="intraday", pattern="^(intraday|eod|closed)$")
    drawdown_floor_cap: float = Field(default=0.0, ge=0)
    drawdown_buffer: float = Field(default=0.0, ge=0)
    trading_halted: bool = False


class PeakRequest(BaseModel):
    peak: Optional[float] = Field(default=None, ge=0)   # None = reiniciar el máximo al valor actual de la cuenta


class MockEventRequest(BaseModel):
    account: Optional[str] = None
    action: Optional[str] = None
    symbol: Optional[str] = None
    quantity: Optional[int] = Field(default=None, gt=0)
    price: Optional[float] = None


class LinkRequest(ExecutionOptions):
    master_account: str = Field(min_length=1)
    multiplier: float = Field(default=1.0, gt=0)
    enabled: bool = True


class AccountSettings(BaseModel):
    enabled: Optional[bool] = None
    alias: Optional[str] = Field(default=None, max_length=40)
    auto: bool = False   # volver a la política automática (activa = conectada)


class MasterRequest(BaseModel):
    account: str = Field(min_length=1)


class ScheduleRequest(BaseModel):
    enabled: bool = False
    window_start: str = Field(default="", pattern=r"^$|^([01]\d|2[0-3]):[0-5]\d$")
    flatten_at: str = Field(default="", pattern=r"^$|^([01]\d|2[0-3]):[0-5]\d$")
    include_master: bool = True


class CommissionsRequest(BaseModel):
    enabled: bool = True
    default_per_side: float = Field(default=2.0, ge=0)
    rates: dict[str, float] = {}
