from .base import DomainModel
from core.enums import OrderSide, OrderStatus
from typing import Optional

class OrderIntent(DomainModel):
    master_account_id: str
    symbol: str
    side: OrderSide
    quantity: int
    order_type: str = "MARKET"
    sl: Optional[float] = None
    tp: Optional[float] = None

class ExecutionReport(DomainModel):
    order_id: str
    account_id: str
    symbol: str
    fill_price: float
    fill_quantity: int
    status: OrderStatus
    execution_time: str
