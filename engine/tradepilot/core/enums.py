from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# Mensajes del addon de NinjaTrader (puerto 5555)
REPLICABLE_MSG_TYPES = {"EXECUTION", "ORDER_PENDING", "ORDER_MODIFIED", "ORDER_CANCELLED"}
MSG_HEARTBEAT = "HEARTBEAT"
MSG_PRICE = "PRICE"
MSG_POSITION = "POSITION"
MSG_ORDER_STATUS = "ORDER_STATUS"   # estado de una orden replicada en un follower (ACK / rechazo)
