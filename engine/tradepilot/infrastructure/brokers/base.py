from abc import ABC, abstractmethod

from tradepilot.core.events import EventBus
from tradepilot.domain.accounts import BridgeHealth, BrokerAccount


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
