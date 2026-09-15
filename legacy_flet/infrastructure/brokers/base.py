from abc import ABC, abstractmethod

class BrokerBridge(ABC):
    @abstractmethod
    async def start(self):
        pass

    @abstractmethod
    async def stop(self):
        pass

    @abstractmethod
    async def get_accounts(self) -> dict:
        pass
