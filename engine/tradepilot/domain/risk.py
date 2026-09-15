from datetime import datetime
from typing import Optional

from pydantic import Field

from tradepilot.core.enums import RiskLevel
from tradepilot.domain import DomainModel


class RiskLimit(DomainModel):
    account_id: str
    max_daily_loss: float = 0.0       # 0 = sin límite
    max_position_size: int = 0        # 0 = sin límite
    trading_halted: bool = False


class RiskState(DomainModel):
    kill_switch: bool = False
    kill_switch_reason: Optional[str] = None
    kill_switch_at: Optional[datetime] = None
    limits: list[RiskLimit] = []


class RiskAlert(DomainModel):
    account_id: str
    level: RiskLevel
    message: str
    violation_value: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)
