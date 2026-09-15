import asyncio
from typing import Dict, List
from domain.accounts import AccountSnapshot, ConnectionStatus
from infrastructure.brokers.ninja_zmq_bridge import NinjaZmqBridge

class AccountService:
    def __init__(self, bridge: NinjaZmqBridge):
        self.bridge = bridge
        self.accounts: Dict[str, AccountSnapshot] = {}
        self.status = ConnectionStatus(broker="NinjaTrader", connected=False)
        self._sync_task = None
        self._running = False

    async def start(self):
        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())

    async def stop(self):
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()

    async def _sync_loop(self):
        while self._running:
            try:
                accounts_data = await self.bridge.get_accounts()
                if accounts_data:
                    self.status.connected = True
                    for acc, bal in accounts_data.items():
                        if acc not in self.accounts:
                            self.accounts[acc] = AccountSnapshot(account_id=acc, balance=bal)
                        else:
                            self.accounts[acc].balance = bal
                else:
                    self.status.connected = False
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.status.connected = False
            await asyncio.sleep(2) 

    def get_all_accounts(self) -> List[AccountSnapshot]:
        return list(self.accounts.values())
