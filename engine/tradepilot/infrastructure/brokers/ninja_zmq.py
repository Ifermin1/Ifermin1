"""Puente NinjaTrader por ZMQ (portado del código Flet original).

Protocolo (sin cambios respecto a la versión anterior):
- SUB tcp://host:5555  -> NinjaTrader publica JSON de eventos del maestro.
- PUB tcp://host:5556  -> el engine publica JSON de órdenes al ejecutor.
- REQ tcp://host:5557  -> "GET_ACCOUNTS_ALL" responde "Sim101|1000.5|Connected|MFF;Sim102|2000|Disconnected|MFF"
                          (addons antiguos: "GET_ACCOUNTS" -> "Sim101|1000.5;Sim102|2000", solo conectadas)
"""
import asyncio
import json
import sys
import time
from datetime import datetime

import zmq
import zmq.asyncio
from loguru import logger

from tradepilot.core.config import settings
from tradepilot.core.events import TOPIC_MASTER_EVENT, EventBus
from tradepilot.domain.accounts import BrokerAccount, BrokerPosition, WorkingOrder
from tradepilot.infrastructure.brokers.base import BrokerBridge


class NinjaZmqBridge(BrokerBridge):
    def __init__(self, bus: EventBus) -> None:
        super().__init__(bus)
        self.health.mode = "ninja"
        self.context = zmq.asyncio.Context()
        self.sub_socket = self.context.socket(zmq.SUB)
        self.pub_socket = self.context.socket(zmq.PUB)
        self.req_socket = self.context.socket(zmq.REQ)
        self.order_socket = self.context.socket(zmq.REQ)   # canal de órdenes con confirmación (addon >= 1.9)
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self._req_lock = asyncio.Lock()
        self._order_lock = asyncio.Lock()
        self._orders_via_req = True   # se desactiva si el addon no conoce ORDER| (versión antigua)
        self.order_timeout_ms = 3000
        self._supports_all = True   # se desactiva si el addon no conoce GET_ACCOUNTS_ALL
        self._supports_orders = True   # GET_ORDERS (addon >= 2.2)
        self._retry_orders_at = 0.0
        self._retry_all_at = 0.0    # cuándo volver a probar GET_ACCOUNTS_ALL (el addon puede actualizarse en caliente)

    async def start(self) -> None:
        host = settings.ZMQ_HOST
        loop = asyncio.get_running_loop()
        if sys.platform == "win32" and not isinstance(loop, asyncio.SelectorEventLoop):
            raise RuntimeError("ZMQ necesita SelectorEventLoop en Windows; arranca con `python -m tradepilot.main`")
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
            self._reset_order_socket(first=True)

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
        for sock in (self.sub_socket, self.pub_socket, self.req_socket, self.order_socket):
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

    async def request(self, message: str, timeout_ms: int = 3000) -> str | None:
        """Petición REQ/REP genérica al addon. None si no responde."""
        if not self._running:
            return None
        async with self._req_lock:
            try:
                await self.req_socket.send_string(message)
                if not await self.req_socket.poll(timeout_ms):
                    self._reset_req_socket()
                    return None
                return await self.req_socket.recv_string()
            except Exception as exc:
                logger.error(f"Error en petición '{message[:40]}': {exc}")
                self._reset_req_socket()
                return None

    async def ping(self) -> bool:
        reply = await self.request("PING", timeout_ms=2000)
        return reply is not None and reply.startswith("PONG")

    async def ping_state(self) -> tuple[bool, str | None, int | None]:
        """Addon >= 1.8 responde "PONG|<boot>|<seq>": permite saber si publica eventos que no nos llegan."""
        reply = await self.request("PING", timeout_ms=2000)
        if reply is None or not reply.startswith("PONG"):
            return False, None, None
        parts = reply.split("|")
        if len(parts) < 3:
            return True, None, None
        try:
            return True, parts[1] or None, int(parts[2])
        except ValueError:
            return True, parts[1] or None, None

    async def resubscribe(self) -> None:
        """Recrea el socket SUB y su tarea de escucha. Se usa cuando el addon responde a comandos
        pero no llegan eventos (típico tras un reinicio de NinjaTrader o del addon)."""
        self.health.resubscribes += 1
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            self.sub_socket.close(linger=0)
        except Exception:
            pass
        self.sub_socket = self.context.socket(zmq.SUB)
        self.sub_socket.connect(f"tcp://{settings.ZMQ_HOST}:{settings.ZMQ_MASTER_PORT}")
        self.sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")
        self._listen_task = asyncio.create_task(self._listen_master())
        # El canal de órdenes sin confirmación (5556) sufre el mismo problema tras un reinicio del addon: recrearlo también.
        try:
            self.pub_socket.close(linger=0)
        except Exception:
            pass
        self.pub_socket = self.context.socket(zmq.PUB)
        self.pub_socket.connect(f"tcp://{settings.ZMQ_HOST}:{settings.ZMQ_FOLLOWER_PORT}")
        self._reset_order_socket()
        logger.warning("Canales ZMQ de eventos (5555) y órdenes (5556/5557) reconectados")

    async def get_positions(self) -> list[BrokerPosition] | None:
        reply = await self.request("GET_POSITIONS")
        if reply is None or reply.startswith("ERROR|"):
            return None
        return self.parse_positions(reply)

    @staticmethod
    def parse_positions(reply: str) -> list[BrokerPosition]:
        out: list[BrokerPosition] = []
        for part in reply.split(";"):
            f = [x.strip() for x in part.split("|")]
            if len(f) < 4 or not f[0]:
                continue
            try:
                qty = int(float(f[3]))
            except ValueError:
                continue
            side = f[2].lower()
            signed = -qty if side == "short" else qty if side == "long" else 0
            try:
                price = float(f[4]) if len(f) > 4 and f[4] else 0.0
            except ValueError:
                price = 0.0
            if signed:
                out.append(BrokerPosition(account_id=f[0], symbol=f[1], quantity=signed, avg_price=price))
        return out

    async def get_orders(self) -> list[tuple[str, WorkingOrder]] | None:
        if not self._supports_orders:
            if time.monotonic() < self._retry_orders_at:
                return None
            self._supports_orders = True
        reply = await self.request("GET_ORDERS")
        if reply is None:
            return None
        if reply.startswith("ERROR|"):
            self._supports_orders = False
            self._retry_orders_at = time.monotonic() + 60
            return None
        return self.parse_orders(reply)

    @staticmethod
    def parse_orders(reply: str) -> list[tuple[str, WorkingOrder]]:
        """acc|orderKey|masterId|action|symbol|qty|filled|type|limit|stop|state;..."""
        out: list[tuple[str, WorkingOrder]] = []
        for part in reply.split(";"):
            f = [x.strip() for x in part.split("|")]
            if len(f) < 11 or not f[0]:
                continue
            try:
                out.append((f[0], WorkingOrder(order_id=f[1], master_order_id=f[2], action=f[3].upper(), symbol=f[4],
                                              quantity=int(float(f[5])), filled=int(float(f[6] or 0)), order_type=f[7].upper(),
                                              limit_price=float(f[8] or 0), stop_price=float(f[9] or 0), state=f[10])))
            except ValueError:
                continue
        return out

    async def flatten(self, account: str) -> str:
        reply = await self.request(f"FLATTEN|{account}", timeout_ms=8000)
        if reply is None:
            raise RuntimeError("NinjaTrader no responde al cierre (¿addon cargado?)")
        if reply.startswith("OK|"):
            logger.warning(f"FLATTEN ejecutado en {account}: {reply}")
            return reply
        if "unknown request" in reply:
            raise RuntimeError("el addon es antiguo: actualiza a v1.5 para cerrar posiciones desde la consola")
        raise RuntimeError(reply.replace("ERROR|", "El addon respondió: "))

    async def watch(self, account: str) -> None:
        await self.request(f"WATCH|{account}")

    async def set_master(self, account: str) -> str:
        reply = await self.request(f"SET_MASTER|{account}")
        if reply is None:
            raise RuntimeError("NinjaTrader no responde (¿addon cargado?)")
        if reply.startswith("OK|"):
            self.health.master_account = reply[3:]
            logger.info(f"Cuenta maestra cambiada en NinjaTrader a {self.health.master_account}")
            return self.health.master_account
        if reply.startswith("ERROR|unknown request"):
            raise RuntimeError("el addon es antiguo: actualiza a v1.2 para cambiar la maestra desde la consola")
        raise RuntimeError(reply.replace("ERROR|", "El addon respondió: "))

    async def get_accounts(self) -> list[BrokerAccount]:
        if not self._running:
            return []
        if not self._supports_all and time.monotonic() >= self._retry_all_at:
            self._supports_all = True   # reintento periódico por si el addon se actualizó
        request = "GET_ACCOUNTS_ALL" if self._supports_all else "GET_ACCOUNTS"
        async with self._req_lock:
            try:
                await self.req_socket.send_string(request)
                if not await self.req_socket.poll(3000):
                    self.health.sync_up = False
                    self.health.connected = False
                    self.health.error_count += 1
                    self._warn_once("Timeout sincronizando cuentas (5557): ¿está NinjaTrader abierto con el addon cargado?")
                    # Un REQ sin respuesta queda bloqueado: lo recreamos.
                    self._reset_req_socket()
                    return []
                response = await self.req_socket.recv_string()
            except Exception as exc:
                self.health.sync_up = False
                self.health.connected = False
                self.health.error_count += 1
                self._warn_once(f"Error {request}: {exc}")
                self._reset_req_socket()
                return []

        if response.startswith("ERROR|"):
            if self._supports_all:
                self._supports_all = False
                self._retry_all_at = time.monotonic() + 60
                self._warn_once("El addon no soporta GET_ACCOUNTS_ALL (versión antigua): solo se verán las cuentas "
                                "conectadas. Actualiza ninjatrader/TradePilotXBridge.cs para ver todas.")
                return await self.get_accounts()
            self._warn_once(f"Respuesta de error del addon: {response}")
            return []

        if not self.health.sync_up:
            logger.info("Sincronización de cuentas con NinjaTrader restablecida")
            self._last_warning = None
        self.health.last_sync = datetime.now()
        self.health.sync_up = True
        self.health.connected = True
        return self.parse_accounts(response)

    @staticmethod
    def parse_accounts(response: str) -> list[BrokerAccount]:
        """'acc|cash' (addon antiguo) o 'acc|cash|status|connection' (GET_ACCOUNTS_ALL)."""
        accounts: list[BrokerAccount] = []
        for part in response.split(";"):
            fields = [f.strip() for f in part.split("|")]
            if len(fields) < 2 or not fields[0]:
                continue
            try:
                balance = float(fields[1])
            except ValueError:
                continue
            connected: bool | None = None
            if len(fields) >= 3 and fields[2]:
                connected = fields[2].lower() == "connected"
            connection = fields[3] if len(fields) >= 4 else ""

            def _num(i: int) -> float:
                try:
                    return float(fields[i]) if len(fields) > i and fields[i] else 0.0
                except ValueError:
                    return 0.0
            accounts.append(BrokerAccount(account_id=fields[0], balance=balance, connected=connected, connection=connection,
                                          realized_pnl=_num(4), unrealized_pnl=_num(5)))
        return accounts

    _last_warning: str | None = None

    def note_addon_version(self, version: str) -> None:
        if self.health.addon_version != version:
            logger.info(f"Addon de NinjaTrader v{version} detectado")
            try:
                if tuple(int(x) for x in version.split(".")[:2]) >= (1, 9) and not self._orders_via_req:
                    self._orders_via_req = True   # el addon se actualizó en caliente: volver al canal con confirmación
            except ValueError:
                pass
        super().note_addon_version(version)
        if not self._supports_all:
            self._supports_all = True   # v1.1+ soporta GET_ACCOUNTS_ALL: reintentar ya
            self._last_warning = None

    def _warn_once(self, msg: str) -> None:
        if msg != self._last_warning:
            logger.warning(msg + " (se silencia hasta que cambie)")
            self._last_warning = msg

    def _reset_req_socket(self) -> None:
        try:
            self.req_socket.close(linger=0)
            self.req_socket = self.context.socket(zmq.REQ)
            self.req_socket.setsockopt(zmq.RCVTIMEO, 3000)
            self.req_socket.setsockopt(zmq.LINGER, 0)
            self.req_socket.connect(f"tcp://{settings.ZMQ_HOST}:{settings.ZMQ_SYNC_PORT}")
        except Exception as exc:
            logger.error(f"No se pudo recrear el socket REQ: {exc}")

    def _reset_order_socket(self, first: bool = False) -> None:
        try:
            if not first:
                self.order_socket.close(linger=0)
                self.order_socket = self.context.socket(zmq.REQ)
            self.order_socket.setsockopt(zmq.LINGER, 0)
            self.order_socket.connect(f"tcp://{settings.ZMQ_HOST}:{settings.ZMQ_SYNC_PORT}")
        except Exception as exc:
            logger.error(f"No se pudo recrear el socket de órdenes: {exc}")

    async def _order_request(self, message: str) -> str | None:
        """Como request() pero por el socket dedicado a órdenes (no espera detrás de la sincronización de cuentas)."""
        async with self._order_lock:
            try:
                await self.order_socket.send_string(message)
                if not await self.order_socket.poll(self.order_timeout_ms):
                    self._reset_order_socket()
                    return None
                return await self.order_socket.recv_string()
            except Exception as exc:
                logger.error(f"Error enviando orden por 5557: {exc}")
                self._reset_order_socket()
                return None

    async def send_order(self, target_account, action, symbol, quantity, order_type, master_order_id,
                         msg_type="EXECUTION", price=0.0, limit_price=0.0, stop_price=0.0, entry=None) -> None:
        if not self._running:
            raise RuntimeError("Puente ZMQ no iniciado")
        payload = {
            "msg_type": msg_type, "account": target_account, "action": action, "symbol": symbol,
            "quantity": quantity, "price": price, "order_type": order_type, "master_order_id": master_order_id,
            "limit_price": limit_price, "stop_price": stop_price,
        }
        if entry:
            payload.update(entry)   # entry_mode / tolerance_ticks / entry_timeout_s / entry_fallback
        raw = json.dumps(payload)
        if self._orders_via_req:
            # Canal con confirmación (addon >= 1.9): si el addon no contesta, se reintenta una vez (el addon ignora
            # duplicados) y si sigue sin contestar se levanta error: nunca una orden "enviada" que nadie recibió.
            reply = await self._order_request("ORDER|" + raw)
            if reply is None:
                self.health.orders_retried += 1
                logger.warning(f"NinjaTrader no confirmó la orden {action} {quantity} {symbol} -> {target_account}: reintentando")
                reply = await self._order_request("ORDER|" + raw)
            if reply is None:
                self.health.error_count += 1
                raise RuntimeError(f"NinjaTrader no confirmó la orden en {2 * self.order_timeout_ms // 1000} s "
                                   "(¿addon cargado? revisa el Output de NinjaTrader)")
            if reply.startswith("ERROR|unknown request"):
                self._orders_via_req = False
                self.health.order_channel = "pub"
                logger.warning("El addon no acepta órdenes por 5557 (anterior a v1.9): se usa 5556 sin confirmación. Actualiza el addon.")
            else:
                self.health.order_channel = "req"
                self.health.last_msg_out = datetime.now()
                if reply.startswith("ERROR|"):
                    self.health.error_count += 1
                    raise RuntimeError("NinjaTrader rechazó la orden: " + reply[6:])
                self.health.orders_confirmed += 1
                if reply.startswith("IGNORED|"):
                    logger.info(f"Orden {action} {quantity} {symbol} -> {target_account} ignorada por el addon: {reply[8:]}")
                else:
                    logger.info(f"Orden confirmada -> {target_account} {action} {quantity} {symbol}")
                return
        try:
            await self.pub_socket.send_string(raw)
            self.health.last_msg_out = datetime.now()
            logger.info(f"Orden publicada (5556, sin confirmación) -> {target_account} {action} {quantity} {symbol}")
        except Exception as exc:
            self.health.error_count += 1
            logger.error(f"Error publicando orden ZMQ: {exc}")
            raise
