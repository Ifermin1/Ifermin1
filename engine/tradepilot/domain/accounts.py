from datetime import datetime
from typing import Optional

from tradepilot.domain import DomainModel


class BridgeHealth(DomainModel):
    mode: str = "mock"
    connected: bool = False
    last_msg_in: Optional[datetime] = None
    last_msg_out: Optional[datetime] = None
    last_sync: Optional[datetime] = None
    error_count: int = 0
    master_feed_up: bool = False     # SUB 5555
    follower_feed_up: bool = False   # PUB 5556
    sync_up: bool = False            # REQ 5557


class PositionSnapshot(DomainModel):
    account_id: str
    symbol: str
    quantity: int
    avg_price: float
    unrealized_pnl: float = 0.0


class AccountSnapshot(DomainModel):
    account_id: str
    balance: float = 0.0
    net_liquidity: float = 0.0
    daily_pnl: float = 0.0
    open_positions: list[PositionSnapshot] = []
    updated_at: datetime
