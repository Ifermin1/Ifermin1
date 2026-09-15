from datetime import datetime
from typing import Optional

from tradepilot.domain import DomainModel


class BridgeHealth(DomainModel):
    mode: str = "mock"
    connected: bool = False
    last_msg_in: Optional[datetime] = None
    last_msg_out: Optional[datetime] = None
    last_sync: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
    master_account: Optional[str] = None   # la publica el addon en cada HEARTBEAT
    addon_version: Optional[str] = None    # >= 1.1 anuncia versión en el HEARTBEAT
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


class BrokerAccount(DomainModel):
    """Lo que el bróker reporta de una cuenta en cada sincronización."""
    account_id: str
    balance: float = 0.0
    connected: Optional[bool] = None   # None = el addon no informa del estado
    connection: str = ""


class AccountSnapshot(DomainModel):
    account_id: str
    balance: float = 0.0
    net_liquidity: float = 0.0
    daily_pnl: float = 0.0
    open_positions: list[PositionSnapshot] = []
    updated_at: datetime
    # gestión desde la consola
    enabled: bool = True               # desactivada = oculta y nunca recibe copias
    alias: str = ""
    connected: Optional[bool] = None
    connection: str = ""
    reported: bool = True              # False = el bróker no la reportó en la última sincronización
