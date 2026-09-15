"""Puente NinjaTrader por ZMQ (portado del código Flet original).

Protocolo (sin cambios respecto a la versión anterior):
- SUB tcp://host:5555  -> NinjaTrader publica JSON de eventos del maestro.
- PUB tcp://host:5556  -> el engine publica JSON de órdenes al ejecutor.
- REQ tcp://host:5557  -> "GET_ACCOUNTS" responde "Sim101|1000.5;Sim102|2000"
"""
import asyncio
import json
from datetime import datetime

import zmq
import zmq.asyncio
from loguru import logger

from tradepilot.core.config import settings
from tradepilot.core.events import TOPIC_MASTER_EVENT, EventBus
from tradepilot.infrastructure.brokers.base import BrokerBridge


class NinjaZmqBridge(BrokerBridge):
    def __init__(self, bus: EventBus) -> None:
        super().__init__(bus)
        self.health.mode = "ninja"
        self.context = zmq.asyncio.Context()
        self.sub_socket = self.context.socket(zmq.SUB)
        self.pub_socket = self.context.socket(zmq.PUB)
        self.req_socket = self.context.socket(zmq.REQ)
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self._req_lock = asyncio.Lock()

    async def start(self) -> None:
        host = settings.ZMQ_HOST
        try:
            self.sub_socket.connect(f"tcp://{host}:{settings.ZMQ_MASTER_PORT}")
            self.sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")
            self.health.master_feed_up = True

            self.pub_socket.connect(f"tcp://{host}:{settings.ZMQ_FOLLOWER_PORT}")
            self.health.follower_feed_up = True

            self.req_socket.setsockopt(zmq.RCVTIMEO, 3000)
            self.req_socket.setsockopt(zmq.LINGER, 0)
            self.req_socket.connect(f"tcp://{host}:{settings.ZMQ_SYNC_PORT}")
            self.health.sync_up = True

            self._running = True
            self._listen_task = asyncio.create_task(self._listen_master())
            logger.info(f"NinjaZmqBridge online (host={host})")
        except Exception as exc:
            self.health.master_feed_up = self.health.follower_feed_up = self.health.sync_up = False
            logger.error(f"Error iniciando NinjaZmqBridge: {exc}")
            raise

    async def stop(self) -> None:
        self._running = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        for sock in (self.sub_socket, self.pub_socket, self.req_socket):
            sock.close(linger=0)
        self.context.term()
        self.health.master_feed_up = self.health.follower_feed_up = self.health.sync_up = False
        self.health.connected = False
        logger.warning("NinjaZmqBridge offline")

    async def _listen_master(self) -> None:
        while self._running:
            try:
                msg = await self.sub_socket.recv_string()
                self.health.last_msg_in = datetime.now()
                try:
                    data = json.loads(msg)
                except json.JSONDecodeError:
                    self.health.error_count += 1
                    logger.error(f"ZMQ JSON inválido: {msg}")
                    continue
                await self.bus.publish(TOPIC_MASTER_EVENT, data)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.health.error_count += 1
                logger.error(f"Error en listener ZMQ: {exc}")

    async def get_accounts(self) -> dict[str, float]:
        if not self._running:
            return {}
        async with self._req_lock:
            try:
                await self.req_socket.send_string("GET_ACCOUNTS")
                if not await self.req_socket.poll(3000):
                    self.health.sync_up = False
                    self.health.connected = False
                    self.health.error_count += 1
                    logger.warning("Timeout sincronizando cuentas (5557)")
                    # Un REQ sin respuesta queda bloqueado: lo recreamos.
                    self._reset_req_socket()
                    return {}
                response = await self.req_socket.recv_string()
            except Exception as exc:
                self.health.sync_up = False
                self.health.connected = False
                self.health.error_count += 1
                logger.error(f"Error GET_ACCOUNTS: {exc}")
                self._reset_req_socket()
                return {}

        self.health.last_sync = datetime.now()
        self.health.sync_up = True
        self.health.connected = True
        accounts: dict[str, float] = {}
        for part in response.split(";"):
            if "|" in part:
                acc, bal = part.split("|", 1)
                try:
                    accounts[acc.strip()] = float(bal)
                except ValueError:
                    pass
        return accounts

    def _reset_req_socket(self) -> None:
        try:
            self.req_socket.close(linger=0)
            self.req_socket = self.context.socket(zmq.REQ)
            self.req_socket.setsockopt(zmq.RCVTIMEO, 3000)
            self.req_socket.setsockopt(zmq.LINGER, 0)
            self.req_socket.connect(f"tcp://{settings.ZMQ_HOST}:{settings.ZMQ_SYNC_PORT}")
        except Exception as exc:
            logger.error(f"No se pudo recrear el socket REQ: {exc}")

    async def send_order(self, target_account, action, symbol, quantity, order_type, master_order_id,
                         msg_type="EXECUTION", price=0.0, limit_price=0.0, stop_price=0.0) -> None:
        if not self._running:
            raise RuntimeError("Puente ZMQ no iniciado")
        payload = {
            "msg_type": msg_type, "account": target_account, "action": action, "symbol": symbol,
            "quantity": quantity, "price": price, "order_type": order_type, "master_order_id": master_order_id,
            "limit_price": limit_price, "stop_price": stop_price,
        }
        try:
            await self.pub_socket.send_string(json.dumps(payload))
            self.health.last_msg_out = datetime.now()
            logger.info(f"Orden publicada -> {target_account} {action} {quantity} {symbol}")
        except Exception as exc:
            self.health.error_count += 1
            logger.error(f"Error publicando orden ZMQ: {exc}")
            raise
