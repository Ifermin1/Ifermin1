from typing import Optional

from pydantic import BaseModel, Field


class RuleCreate(BaseModel):
    master_account: str = Field(min_length=1)
    follower_account: str = Field(min_length=1)
    multiplier: float = Field(default=1.0, gt=0)
    symbol_filter: Optional[str] = None
    enabled: bool = True


class RuleUpdate(BaseModel):
    master_account: Optional[str] = None
    follower_account: Optional[str] = None
    multiplier: Optional[float] = Field(default=None, gt=0)
    symbol_filter: Optional[str] = None
    enabled: Optional[bool] = None


class KillSwitchRequest(BaseModel):
    active: bool
    reason: Optional[str] = None


class RiskLimitUpsert(BaseModel):
    account_id: str = Field(min_length=1)
    max_daily_loss: float = Field(default=0.0, ge=0)
    max_position_size: int = Field(default=0, ge=0)
    trading_halted: bool = False


class MockEventRequest(BaseModel):
    account: Optional[str] = None
    action: Optional[str] = None
    symbol: Optional[str] = None
    quantity: Optional[int] = Field(default=None, gt=0)
    price: Optional[float] = None


class LinkRequest(BaseModel):
    master_account: str = Field(min_length=1)
    multiplier: float = Field(default=1.0, gt=0)
    enabled: bool = True


class AccountSettings(BaseModel):
    enabled: Optional[bool] = None
    alias: Optional[str] = Field(default=None, max_length=40)
    auto: bool = False   # volver a la política automática (activa = conectada)
