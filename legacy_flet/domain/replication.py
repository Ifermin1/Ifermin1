from .base import DomainModel
from typing import Optional
from datetime import datetime
from pydantic import Field

class MasterEvent(DomainModel):
    msg_type: str
    account: str
    action: str
    symbol: str
    quantity: int
    price: float
    order_type: str
    state: str
    order_id: str
    timestamp: datetime = Field(default_factory=datetime.now)

class ReplicationRule(DomainModel):
    id: str
    master_account: str
    follower_account: str
    multiplier: float = 1.0
    symbol_filter: Optional[str] = None
    enabled: bool = True

class ReplicationTask(DomainModel):
    rule_id: str
    master_event: MasterEvent
    target_account: str
    scaled_quantity: int
    master_order_id: str
    status: str = "PENDING"
