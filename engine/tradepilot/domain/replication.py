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
        """Misma orden en distintos estados (Accepted/Working) = mismo evento. Un fill se
        distingue por execution_id; una modificación, por sus precios."""
        return (self.msg_type, self.account, self.order_id, self.quantity, self.execution_id,
                round(self.limit_price, 6), round(self.stop_price, 6))


class ReplicationRule(DomainModel):
    id: str
    master_account: str
    follower_account: str
    multiplier: float = 1.0
    symbol_filter: Optional[str] = None
    enabled: bool = True
    # Nivel 3: ejecución
    target_root: Optional[str] = None      # p. ej. "MNQ": la seguidora opera el micro del símbolo de la maestra
    entry_mode: str = "market"             # "market" | "limit" (límite al precio del maestro +/- tolerancia)
    tolerance_ticks: int = 2
    entry_timeout_s: int = 5               # si la límite no se llena en este tiempo...
    entry_fallback: str = "market"         # ... "market" (a mercado lo que falte) | "cancel"

    @field_validator("target_root", mode="before")
    @classmethod
    def _root(cls, v):
        if isinstance(v, str):
            v = v.strip().upper().split(" ")[0]
            return v or None
        return v

    @field_validator("entry_mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        v = (v or "market").lower()
        if v not in ("market", "limit"):
            raise ValueError("entry_mode debe ser market o limit")
        return v

    @field_validator("entry_fallback")
    @classmethod
    def _fallback(cls, v: str) -> str:
        v = (v or "market").lower()
        if v not in ("market", "cancel"):
            raise ValueError("entry_fallback debe ser market o cancel")
        return v

    def map_symbol(self, symbol: str) -> str:
        """'NQ DEC26' -> 'MNQ DEC26' si la regla tiene target_root."""
        if not self.target_root:
            return symbol
        parts = symbol.strip().split(" ", 1)
        return self.target_root + (" " + parts[1] if len(parts) > 1 else "")

    def map_root(self, root: str) -> str:
        return self.target_root or root.upper()

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

    @field_validator("follower_account")
    @classmethod
    def _not_self(cls, v: str, info):
        master = info.data.get("master_account", "")
        if v.strip().lower() == str(master).strip().lower():
            raise ValueError("maestro y seguidor no pueden ser la misma cuenta")
        return v

    def matches(self, event: MasterEvent) -> bool:
        return self.enabled and self.master_matches(event.account) and self.symbol_matches(event.symbol)

    def master_matches(self, account: str) -> bool:
        return self.master_account.strip().lower() == (account or "").strip().lower()

    def symbol_matches(self, symbol: str) -> bool:
        """Sin filtro: todo. Con filtro: nombre exacto ("NQ SEP26") o raíz ("NQ"),
        para que no importe si NinjaTrader lo llama "NQ 12-26" o "NQ SEP26"."""
        if not self.symbol_filter:
            return True
        sym = (symbol or "").strip().upper()
        flt = self.symbol_filter
        return sym == flt or sym.split(" ")[0] == flt.split(" ")[0]

    def scale(self, quantity: int) -> int:
        return int(quantity * self.multiplier)


class ReplicationTask(DomainModel):
    rule_id: str
    master_event: MasterEvent
    target_account: str
    scaled_quantity: int
    master_order_id: str
    status: str = "PENDING"
