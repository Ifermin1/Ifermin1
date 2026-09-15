from datetime import datetime
from typing import Optional

from pydantic import Field, field_validator
from loguru import logger

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
    # Campos extra que manda el addon de NinjaTrader
    limit_price: float = 0.0
    stop_price: float = 0.0
    execution_id: str = ""
    master_order_id: str = ""      # si viene relleno, es el ACK/fill de un follower, no una orden del maestro
    timestamp: datetime = Field(default_factory=datetime.now)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _lenient_timestamp(cls, v):
        """Una operación nunca se descarta por el formato de la fecha."""
        if v is None or v == "" or isinstance(v, datetime):
            return v or datetime.now()
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            pass
        import re
        m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?", str(v))
        if m:
            frac = (m.group(2) or "")[:6].ljust(6, "0")
            tz = (m.group(3) or "").replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(f"{m.group(1)}.{frac}{tz}")
            except ValueError:
                pass
        logger.warning(f"timestamp no reconocido ({v!r}); se usa la hora local")
        return datetime.now()

    @property
    def is_follower_ack(self) -> bool:
        return bool(self.master_order_id)

    @property
    def dedup_key(self) -> tuple:
        return (self.msg_type, self.account, self.order_id, self.quantity, self.execution_id, self.state)


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
