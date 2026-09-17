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
    addon_boot: Optional[str] = None       # id de arranque del addon visto por el canal de eventos (>= 1.8)
    addon_boot_req: Optional[str] = None   # id de arranque según el canal de comandos (PING)
    addon_seq_req: Optional[int] = None    # último seq publicado según el canal de comandos (PING)
    resubscribes: int = 0                  # reconexiones del canal de eventos
    order_channel: str = "pub"             # "req" = órdenes con confirmación por 5557 (addon >= 1.9); "pub" = 5556 sin confirmación
    orders_confirmed: int = 0              # órdenes confirmadas por el addon
    orders_retried: int = 0                # órdenes reenviadas por falta de confirmación
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


class WorkingOrder(DomainModel):
    """Orden viva en el bróker (stop, TP, entrada pendiente). El addon >= 2.2 las reporta con GET_ORDERS."""
    order_id: str
    master_order_id: str = ""     # "" en las órdenes propias de la maestra; id de la orden maestra en las copias
    action: str
    symbol: str
    quantity: int
    filled: int = 0
    order_type: str = "MARKET"
    limit_price: float = 0.0
    stop_price: float = 0.0
    state: str = "Working"


class BrokerAccount(DomainModel):
    """Lo que el bróker reporta de una cuenta en cada sincronización."""
    account_id: str
    balance: float = 0.0
    connected: Optional[bool] = None   # None = el addon no informa del estado
    connection: str = ""
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0


class BrokerPosition(DomainModel):
    account_id: str
    symbol: str
    quantity: int          # con signo: >0 largo, <0 corto
    avg_price: float = 0.0


class DrawdownSnapshot(DomainModel):
    """Drawdown dinámico (trailing) de la cuenta, como lo mide un prop firm: la distancia entre el máximo que llegó a
    valer la cuenta (marca de agua) y lo que vale ahora. `equity` = balance + flotante."""
    equity: float = 0.0
    mode: str = "intraday"                  # "intraday" (dinámico, con flotante) | "eod" (balance al cierre del día) | "closed"
    peak: float = 0.0                       # máximo alcanzado según el modo
    peak_at: Optional[datetime] = None
    drawdown: float = 0.0                   # peak - valor actual (>= 0)
    limit: float = 0.0                      # drawdown máximo configurado (0 = solo se observa)
    floor: Optional[float] = None           # nivel al que el prop firm cierra la cuenta (peak - limit, con tope si lo hay)
    room: Optional[float] = None            # cuánto puede perder aún antes de tocar el suelo
    pct: Optional[float] = None             # % del drawdown permitido ya consumido
    buffer: float = 0.0                     # colchón: el engine cierra cuando room <= buffer
    locked: bool = False                    # el suelo ya no sube (llegó al tope configurado)


class AccountSnapshot(DomainModel):
    account_id: str
    balance: float = 0.0
    net_liquidity: float = 0.0
    daily_pnl: float = 0.0
    drawdown: DrawdownSnapshot = DrawdownSnapshot()
    open_positions: list[PositionSnapshot] = []
    working_orders: list[WorkingOrder] = []
    updated_at: datetime
    # gestión desde la consola
    enabled: bool = True               # desactivada = oculta y nunca recibe copias
    enabled_source: str = "auto"       # "auto": sigue el estado de conexión; "user": fijada a mano
    alias: str = ""
    connected: Optional[bool] = None
    connection: str = ""
    reported: bool = True              # False = el bróker no la reportó en la última sincronización
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    # sincronización con la maestra (solo seguidoras vinculadas)
    desync: bool = False
    desync_detail: str = ""
