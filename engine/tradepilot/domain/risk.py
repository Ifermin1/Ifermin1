from datetime import datetime
from typing import Optional

from pydantic import Field

from tradepilot.core.enums import RiskLevel
from tradepilot.domain import DomainModel


class RiskLimit(DomainModel):
    account_id: str
    max_daily_loss: float = 0.0       # 0 = sin límite (USD, P&L del día realizado + flotante)
    max_daily_profit: float = 0.0     # 0 = sin objetivo (USD): al alcanzarlo se pausa y cierra para asegurar la ganancia
    max_position_size: int = 0        # 0 = sin límite (contratos por orden y de posición resultante)
    trading_halted: bool = False
    halted_reason: str = ""           # "" manual; "daily_loss" / "daily_profit" cuando lo pausó el engine
    halted_at: Optional[datetime] = None


class Schedule(DomainModel):
    """Ventana horaria (hora local del PC del engine)."""
    enabled: bool = False
    window_start: str = ""            # "HH:MM": no copiar antes; "" = sin límite
    flatten_at: str = ""              # "HH:MM": cerrar todo y bloquear hasta el día siguiente; "" = sin cierre
    include_master: bool = True
    last_flatten_day: str = ""        # AAAA-MM-DD del último cierre programado ejecutado


class RiskState(DomainModel):
    kill_switch: bool = False
    kill_switch_reason: Optional[str] = None
    kill_switch_at: Optional[datetime] = None
    limits: list[RiskLimit] = []
    schedule: Schedule = Schedule()
    session_closed: bool = False      # cierre programado ejecutado hoy: no se copia hasta mañana
    addon_silent: bool = False        # sin heartbeat del addon


class RiskAlert(DomainModel):
    account_id: str
    level: RiskLevel
    message: str
    violation_value: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)
