import asyncio
import zmq
import zmq.asyncio
import json
from loguru import logger
from core.config import settings
from bus.event_bus import bus
from .base import BrokerBridge
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class BridgeHealth(BaseModel):
    last_msg_in: Optional[datetime] = None
    last_msg_out: Optional[datetime] = None
    last_sync: Optional[datetime] = None
    error_count: int = 0
    status_5555: bool = False
    status_5556: bool = False
    status_5557: bool = False

class NinjaZmqBridge(BrokerBridge):
    def __init__(self):
        self.context = zmq.asyncio.Context()
        self.sub_socket = self.context.socket(zmq.SUB)
        self.pub_socket = self.context.socket(zmq.PUB)
        self.req_socket = self.context.socket(zmq.REQ)
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self.health = BridgeHealth()

    async def start(self):
        try:
            host = settings.ZMQ_HOST
            
            # SUB: Listen to Master updates
            self.sub_socket.connect(f"tcp://{host}:{settings.ZMQ_MASTER_PORT}")
            self.sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")
            self.health.status_5555 = True
            
            # PUB: Send orders to Executor
            self.pub_socket.connect(f"tcp://{host}:{settings.ZMQ_FOLLOWER_PORT}")
            self.health.status_5556 = True
            
            # REQ: Sync accounts
            self.req_socket.connect(f"tcp://{host}:{settings.ZMQ_SYNC_PORT}")
            self.health.status_5557 = True
            
            self._running = True
            logger.info(f"✅ NinjaZmqBridge Online (HOST={host})")
            self._listen_task = asyncio.create_task(self._listen_master())
        except Exception as e:
            self.health.status_5555 = False
            self.health.status_5556 = False
            self.health.status_5557 = False
            logger.error(f"Error iniciando NinjaZmqBridge: {e}")
            raise

    async def stop(self):
        self._running = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        
        self.sub_socket.close(linger=0)
        self.pub_socket.close(linger=0)
        self.req_socket.close(linger=0)
        self.context.term()
        
        self.health.status_5555 = False
        self.health.status_5556 = False
        self.health.status_5557 = False
        logger.warning("NinjaZmqBridge Offline cleanly terminated.")

    async def _listen_master(self):
        while self._running:
            try:
                # Read without block implicitly handled by asyncio
                msg = await self.sub_socket.recv_string()
                self.health.last_msg_in = datetime.now()
                try:
                    data = json.loads(msg)
                    # Forward to event bus
                    await bus.publish("ninja.master.event", data)
                except json.JSONDecodeError:
                    self.health.error_count += 1
                    logger.error(f"ZMQ JSON inválido: {msg}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.health.error_count += 1
                logger.error(f"Error en ZMQ listener loop: {e}")

    async def get_accounts(self) -> dict:
        if not self._running: return {}
        try:
            await self.req_socket.send_string("GET_ACCOUNTS")
            if await self.req_socket.poll(3000):
                response = await self.req_socket.recv_string()
                self.health.last_sync = datetime.now()
                self.health.status_5557 = True
                accounts = {}
                for part in response.split(";"):
                    if "|" in part:
                        acc, bal = part.split("|", 1)
                        try:
                            accounts[acc] = float(bal)
                        except:
                            pass
                return accounts
            else:
                self.health.status_5557 = False
                self.health.error_count += 1
                logger.warning("Timeout syncing accounts from 5557")
                return {}
        except Exception as e:
            self.health.status_5557 = False
            self.health.error_count += 1
            logger.error(f"Error GET_ACCOUNTS: {e}")
            return {}

    async def send_order(self, target_account: str, action: str, symbol: str, quantity: int, order_type: str, master_order_id: str, msg_type: str = "EXECUTION", price: float = 0.0):
        if not self._running: return
        payload = {
            "msg_type": msg_type,
            "account": target_account,
            "action": action,
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "order_type": order_type,
            "master_order_id": master_order_id
        }
        try:
            await self.pub_socket.send_string(json.dumps(payload))
            self.health.last_msg_out = datetime.now()
            logger.info(f"📤 Orden publicada a Ninja Executor -> {target_account} {action} {quantity} {symbol}")
        except Exception as e:
            self.health.error_count += 1
            logger.error(f"Error publicando orden ZMQ: {e}")
            raise e
