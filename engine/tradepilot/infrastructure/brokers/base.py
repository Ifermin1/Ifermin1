from abc import ABC, abstractmethod

from tradepilot.core.events import EventBus
from tradepilot.domain.accounts import BridgeHealth, BrokerAccount, BrokerPosition


class BrokerBridge(ABC):
    """Contrato de un puente de bróker.

    Cualquier implementación (NinjaTrader por ZMQ, simulador, en el futuro
    Rithmic/Tradovate...) publica los eventos del maestro en el bus con el
    tópico TOPIC_MASTER_EVENT y expone salud, cuentas y envío de órdenes.
    """

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.health = BridgeHealth()

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    def note_addon_version(self, version: str) -> None:
        """El addon anuncia su versión en el HEARTBEAT; las implementaciones pueden reaccionar."""
        self.health.addon_version = version

    async def get_positions(self) -> list[BrokerPosition] | None:
        """Posiciones abiertas de todas las cuentas. None = el puente no lo soporta."""
        return None

    async def flatten(self, account: str) -> str:
        """Cancela todas las órdenes y cierra las posiciones de la cuenta. Lanza RuntimeError si falla."""
        raise RuntimeError("este puente no permite cerrar posiciones")

    async def watch(self, account: str) -> None:
        """Pide al bróker que reporte órdenes/posiciones de esa cuenta (no-op si no aplica)."""

    async def ping(self) -> bool:
        """¿El bróker responde a consultas? (canal de comandos)"""
        return True

    async def resubscribe(self) -> None:
        """Reconecta el canal de eventos (heartbeat/operaciones) sin tocar el de comandos."""

    async def set_master(self, account: str) -> str:
        """Cambia la cuenta maestra en el bróker. Devuelve el nombre aplicado o lanza RuntimeError."""
        raise RuntimeError("este puente no permite cambiar la maestra")

    @abstractmethod
    async def get_accounts(self) -> list[BrokerAccount]:
        """Cuentas que el bróker conoce (conectadas o no, si el addon lo soporta)."""

    @abstractmethod
    async def send_order(
        self,
        target_account: str,
        action: str,
        symbol: str,
        quantity: int,
        order_type: str,
        master_order_id: str,
        msg_type: str = "EXECUTION",
        price: float = 0.0,
        limit_price: float = 0.0,
        stop_price: float = 0.0,
    ) -> None: ...
