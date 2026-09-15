import asyncio
import zmq
import zmq.asyncio
from loguru import logger
from typing import Callable, Any, Dict
from core.config import settings

class EventBus:
    def __init__(self):
        self.context = zmq.asyncio.Context()
        self.pub_socket = self.context.socket(zmq.PUB)
        self.sub_socket = self.context.socket(zmq.SUB)
        self._callbacks: Dict[str, list[Callable]] = {}
        self._running = False
        self._listen_task: asyncio.Task | None = None

    async def start(self):
        """Startup limpio de sockets."""
        try:
            self.pub_socket.bind(settings.zmq_pub_url)
            self.sub_socket.connect(settings.zmq_pub_url)
            self.sub_socket.subscribe("")
            self._running = True
            self._listen_task = asyncio.create_task(self._listen())
            logger.info(f"🚀 EventBus Online: {settings.zmq_pub_url}")
        except Exception as e:
            logger.error(f"Fallo al iniciar EventBus: {e}")
            raise

    async def stop(self):
        """Shutdown ordenado de recursos ZMQ."""
        logger.warning("Cerrando EventBus...")
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
        
        self.pub_socket.close(linger=0)
        self.sub_socket.close(linger=0)
        self.context.term()
        logger.info("EventBus Offline.")

    async def publish(self, topic: str, data: Any):
        if not self._running: return
        await self.pub_socket.send_json({"topic": topic, "data": data})

    def subscribe(self, topic: str, callback: Callable):
        if topic not in self._callbacks:
            self._callbacks[topic] = []
        self._callbacks[topic].append(callback)

    async def _listen(self):
        while self._running:
            try:
                msg = await self.sub_socket.recv_json()
                topic, data = msg.get("topic"), msg.get("data")
                for cb in self._callbacks.get(topic, []):
                    if asyncio.iscoroutinefunction(cb): await cb(data)
                    else: cb(data)
            except asyncio.CancelledError: break
            except Exception as e: logger.error(f"Error listen: {e}")

bus = EventBus()
