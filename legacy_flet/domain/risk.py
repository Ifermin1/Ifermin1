from .base import DomainModel
from core.enums import RiskLevel

class RiskLimit(DomainModel):
    account_id: str
    max_daily_loss: float
    max_position_size: int
    max_drawdown: float
    trading_halted: bool = False

class RiskAlert(DomainModel):
    account_id: str
    level: RiskLevel
    message: str
    violation_value: float
