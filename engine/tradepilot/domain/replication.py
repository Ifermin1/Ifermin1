from datetime import datetime
from typing import Optional

from pydantic import Field, field_validator

from tradepilot.domain import DomainModel


class MasterEvent(DomainModel):
    """Mensaje que NinjaTrader publica cuando el maestro opera."""
    msg_type: str
    account: str
    action: str
    symbol: str
    quantity: int
    price: float = 0.0
    order_type: str = "MARKET"
    state: str = ""
    order_id: str
    timestamp: datetime = Field(default_factory=datetime.now)


class ReplicationRule(DomainModel):
    id: str
    master_account: str
    follower_account: str
    multiplier: float = 1.0
    symbol_filter: Optional[str] = None
    enabled: bool = True

    @field_validator("multiplier")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("multiplier debe ser > 0")
        return v

    @field_validator("symbol_filter", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip().upper() if isinstance(v, str) else v

    def matches(self, event: MasterEvent) -> bool:
        if not self.enabled or self.master_account != event.account:
            return False
        if self.symbol_filter and self.symbol_filter != event.symbol.upper():
            return False
        return True

    def scale(self, quantity: int) -> int:
        return int(quantity * self.multiplier)


class ReplicationTask(DomainModel):
    rule_id: str
    master_event: MasterEvent
    target_account: str
    scaled_quantity: int
    master_order_id: str
    status: str = "PENDING"
