from .base import DomainModel
from typing import List, Optional

class ConnectionStatus(DomainModel):
    broker: str
    connected: bool
    last_ping: Optional[str] = None

class PositionSnapshot(DomainModel):
    account_id: str
    symbol: str
    quantity: int
    avg_price: float
    unrealized_pnl: float

class AccountSnapshot(DomainModel):
    account_id: str
    balance: float = 0.0
    net_liquidity: float = 0.0
    used_margin: float = 0.0
    available_funds: float = 0.0
    daily_pnl: float = 0.0
    open_positions: List[PositionSnapshot] = []
