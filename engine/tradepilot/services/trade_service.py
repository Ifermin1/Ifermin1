"""Operativa manual desde la consola (pestaña Operar): poner, modificar y cancelar órdenes y cerrar posiciones en cualquier
cuenta, también en la maestra (entonces la orden se replica a las seguidoras como una más).

Diseñado para que un retraso o un corte nunca cueste dinero:
- cada orden lleva un `client_id` único que genera la consola: si la respuesta se pierde y se reenvía, el engine (y el addon)
  reconocen el id y devuelven el mismo resultado, nunca una orden duplicada;
- el engine solo responde "aceptada" cuando NinjaTrader la ha confirmado por el canal de órdenes (REQ/REP con reintento);
  si no confirma en MANUAL_CONFIRM_TIMEOUT_S la respuesta es un error claro ("NO confirmada: comprueba NinjaTrader");
- validaciones antes de enviar: cuenta conocida y activa, cantidad ≤ MANUAL_MAX_QTY, precios según el tipo, kill switch y
  cuentas pausadas por riesgo (solo se permite cerrar), addon con versión suficiente;
- cada acción queda en la auditoría (MANUAL_ORDER / MANUAL_CANCEL / MANUAL_MODIFY / MANUAL_REJECTED) con la latencia medida.
"""
import time
from collections import OrderedDict
from datetime import datetime

from loguru import logger

from tradepilot.domain.symbols import tick_size

ACTIONS = ("BUY", "SELL")
TYPES = ("MARKET", "LIMIT", "STOPMARKET", "STOPLIMIT")


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


class TradeService:
    def __init__(self, bridge, accounts, risk, audit, settings) -> None:
        self.bridge = bridge
        self.accounts = accounts
        self.risk = risk
        self.audit = audit
        self.settings = settings
        self._done: OrderedDict[str, dict] = OrderedDict()     # client_id -> respuesta (idempotencia)
        self._inflight: set[str] = set()
        self.stats = {"orders": 0, "rejected": 0, "cancels": 0, "modifies": 0, "unconfirmed": 0}

    # ---- comprobaciones ----
    def _check_addon(self) -> None:
        if self.settings.ENGINE_MODE != "ninja":
            return
        v = self.bridge.health.addon_version
        if v is None or _ver(v) < _ver(self.settings.MANUAL_ADDON_VERSION):
            raise ValueError(f"operar desde la consola requiere el addon v{self.settings.MANUAL_ADDON_VERSION} o superior "
                             f"(instalado: {v or 'desconocido'}). Recompila ninjatrader/TradePilotXBridge.cs en NinjaTrader")
        if not self.bridge.health.connected:
            raise ValueError("NinjaTrader no está conectado al engine")

    def _check_account(self, account: str, closing: bool) -> str:
        snap = self.accounts.find(account)
        if snap is None:
            raise ValueError(f"cuenta desconocida: {account}")
        if not snap.enabled and not closing:
            raise ValueError(f"{snap.account_id} está oculta/desactivada: actívala en Cuentas antes de operarla")
        if not closing:
            if self.risk.kill_switch:
                raise ValueError("kill switch activo: solo se permite cerrar. Pulsa Reanudar en la barra de estado para volver a operar")
            lim = self.risk.limits.get(snap.account_id)
            if lim is not None and lim.trading_halted:
                raise ValueError(f"{snap.account_id} está pausada por riesgo ({lim.halted_reason or 'límite'}): solo se permite cerrar; reanúdala en Riesgo")
        return snap.account_id

    def validate(self, req: dict) -> dict:
        """Normaliza y valida la petición; devuelve el payload que se manda al addon."""
        account = self._check_account(str(req.get("account", "")).strip(), closing=bool(req.get("closing")))
        symbol = " ".join(str(req.get("symbol", "")).split())
        if not symbol or len(symbol) > 24:
            raise ValueError("indica el instrumento (p. ej. 'NQ 12-26', tal como aparece en NinjaTrader)")
        action = str(req.get("action", "")).upper()
        if action not in ACTIONS:
            raise ValueError("la acción debe ser BUY o SELL")
        qty = int(req.get("quantity", 0))
        if qty <= 0:
            raise ValueError("la cantidad debe ser mayor que 0")
        if qty > self.settings.MANUAL_MAX_QTY and not req.get("closing"):
            raise ValueError(f"máximo {self.settings.MANUAL_MAX_QTY} contratos por orden desde la consola (MANUAL_MAX_QTY en engine/.env)")
        otype = str(req.get("order_type", "MARKET")).upper().replace("_", "").replace(" ", "")
        if otype == "STOP":
            otype = "STOPMARKET"
        if otype not in TYPES:
            raise ValueError("tipo de orden no admitido (MARKET, LIMIT, STOPMARKET, STOPLIMIT)")
        limit = float(req.get("limit_price") or 0)
        stop = float(req.get("stop_price") or 0)
        if otype in ("LIMIT", "STOPLIMIT") and limit <= 0:
            raise ValueError("precio límite obligatorio")
        if otype in ("STOPMARKET", "STOPLIMIT") and stop <= 0:
            raise ValueError("precio del stop obligatorio")
        sl, tp = int(req.get("sl_ticks") or 0), int(req.get("tp_ticks") or 0)
        if sl < 0 or tp < 0 or sl > 2000 or tp > 2000:
            raise ValueError("stop y objetivo en ticks deben estar entre 0 y 2000")
        if otype != "MARKET" and (sl or tp):
            raise ValueError("el stop/objetivo automático solo se admite en órdenes a mercado (en límite o stop ponlos después, cuando se ejecute)")
        cid = str(req.get("client_id", "")).strip()
        if not cid or len(cid) > 48 or not all(ch.isalnum() or ch in "-_" for ch in cid):
            raise ValueError("client_id inválido")
        return {"id": cid, "account": account, "symbol": symbol, "action": action, "quantity": qty, "order_type": otype,
                "limit_price": limit if otype in ("LIMIT", "STOPLIMIT") else 0.0, "stop_price": stop if otype in ("STOPMARKET", "STOPLIMIT") else 0.0,
                "sl_ticks": sl, "tp_ticks": tp, "tick": tick_size(symbol), "note": str(req.get("note") or "")[:80]}

    # ---- acciones ----
    def _remember(self, cid: str, result: dict) -> dict:
        self._done[cid] = result
        while len(self._done) > 500:
            self._done.popitem(last=False)
        return result

    async def place(self, req: dict) -> dict:
        cid = str(req.get("client_id", "")).strip()
        if cid and cid in self._done:
            return {**self._done[cid], "duplicate": True}
        if cid and cid in self._inflight:
            raise ValueError("esa orden ya se está enviando; espera la confirmación")
        try:
            self._check_addon()
            order = self.validate(req)
        except ValueError as exc:
            self.stats["rejected"] += 1
            self.audit.log("MANUAL_REJECTED", f"Orden desde la consola rechazada: {exc}", target=str(req.get("account") or ""), details={"request": req})
            raise
        self._inflight.add(cid)
        t0 = time.monotonic()
        try:
            reply = await self.bridge.manual_order(order)
        except Exception as exc:
            ms = round((time.monotonic() - t0) * 1000)
            self.stats["unconfirmed" if "no confirmó" in str(exc) else "rejected"] += 1
            self.audit.log("MANUAL_REJECTED", f"{order['account']}: {order['action']} {order['quantity']} {order['symbol']} {order['order_type']} NO aceptada ({ms} ms): {exc}",
                           target=order["account"], details={"client_id": cid, "order": order, "ms": ms})
            raise RuntimeError(str(exc))
        finally:
            self._inflight.discard(cid)
        ms = round((time.monotonic() - t0) * 1000)
        self.bridge.health.last_order_ms = ms
        self.stats["orders"] += 1
        parts = reply.split("|")
        result = {"client_id": cid, "order_id": parts[1] if len(parts) > 1 else "", "status": parts[2] if len(parts) > 2 else "accepted",
                  "detail": reply, "ms": ms, "account": order["account"], "symbol": order["symbol"], "action": order["action"], "quantity": order["quantity"],
                  "order_type": order["order_type"], "at": datetime.now().astimezone().isoformat(timespec="milliseconds")}
        price = f" @ {order['limit_price']:g}" if order["limit_price"] else (f" stop {order['stop_price']:g}" if order["stop_price"] else "")
        brackets = (f" · SL {order['sl_ticks']} ticks" if order["sl_ticks"] else "") + (f" · TP {order['tp_ticks']} ticks" if order["tp_ticks"] else "")
        master = (self.bridge.health.master_account or "").lower() == order["account"].lower()
        self.audit.log("MANUAL_ORDER", f"{order['account']}: {order['action']} {order['quantity']} {order['symbol']} {order['order_type']}{price}{brackets} "
                       f"aceptada por NinjaTrader en {ms} ms{' · es la maestra: se replica a las seguidoras' if master else ''}{' · ' + order['note'] if order['note'] else ''}",
                       target=order["account"], details={"client_id": cid, "reply": reply, "ms": ms, "order": order})
        return self._remember(cid, result)

    async def cancel(self, account: str, order_id: str) -> dict:
        self._check_addon()
        acc = self._check_account(account, closing=True)
        t0 = time.monotonic()
        try:
            reply = await self.bridge.cancel_order(acc, order_id)
        except Exception as exc:
            self.audit.log("MANUAL_REJECTED", f"{acc}: no se pudo cancelar {order_id}: {exc}", target=acc)
            raise RuntimeError(str(exc))
        ms = round((time.monotonic() - t0) * 1000)
        self.stats["cancels"] += 1
        self.audit.log("MANUAL_CANCEL", f"{acc}: orden {order_id} cancelada desde la consola ({ms} ms)", target=acc, details={"order_id": order_id, "reply": reply, "ms": ms})
        return {"account": acc, "order_id": order_id, "detail": reply, "ms": ms}

    async def modify(self, account: str, order_id: str, quantity: int, limit_price: float, stop_price: float) -> dict:
        self._check_addon()
        acc = self._check_account(account, closing=True)
        if quantity < 0 or limit_price < 0 or stop_price < 0:
            raise ValueError("valores negativos")
        if quantity > self.settings.MANUAL_MAX_QTY:
            raise ValueError(f"máximo {self.settings.MANUAL_MAX_QTY} contratos")
        t0 = time.monotonic()
        try:
            reply = await self.bridge.modify_order(acc, order_id, int(quantity), float(limit_price), float(stop_price))
        except Exception as exc:
            self.audit.log("MANUAL_REJECTED", f"{acc}: no se pudo modificar {order_id}: {exc}", target=acc)
            raise RuntimeError(str(exc))
        ms = round((time.monotonic() - t0) * 1000)
        self.stats["modifies"] += 1
        self.audit.log("MANUAL_MODIFY", f"{acc}: orden {order_id} modificada desde la consola (cant. {quantity or '='}, límite {limit_price or '='}, stop {stop_price or '='}) ({ms} ms)",
                       target=acc, details={"order_id": order_id, "reply": reply, "ms": ms})
        return {"account": acc, "order_id": order_id, "detail": reply, "ms": ms}

    async def close_position(self, account: str, symbol: str, client_id: str) -> dict:
        """Cierra solo la posición de ese instrumento con una orden a mercado contraria (a diferencia de Cerrar cuenta, que
        cancela todo y cierra todos los instrumentos)."""
        snap = self.accounts.find(account)
        if snap is None:
            raise ValueError(f"cuenta desconocida: {account}")
        pos = next((p for p in snap.open_positions if p.symbol.lower() == symbol.lower() and p.quantity), None)
        if pos is None:
            raise ValueError(f"{snap.account_id} no tiene posición en {symbol}")
        return await self.place({"client_id": client_id, "account": snap.account_id, "symbol": pos.symbol, "action": "SELL" if pos.quantity > 0 else "BUY",
                                 "quantity": abs(pos.quantity), "order_type": "MARKET", "closing": True, "note": "cierre de posición desde la consola"})

    def on_manual_status(self, data: dict) -> None:
        """MANUAL_STATUS del addon (v2.7): ciclo de vida de una orden manual en cualquier cuenta (aceptada, ejecutada, rechazada)."""
        state = str(data.get("state") or "")
        acc = str(data.get("account") or "")
        err = str(data.get("error") or "")
        what = f"{data.get('action')} {data.get('quantity')} {data.get('symbol')} {data.get('order_type')}"
        if err or state.lower() == "rejected":
            self.stats["rejected"] += 1
            self.audit.log("MANUAL_REJECTED", f"{acc}: {what} rechazada por el bróker: {err or state} {data.get('native_error') or ''}".strip(),
                           target=acc, details=data)
        elif state.lower() == "filled":
            self.audit.log("MANUAL_FILL", f"{acc}: {what} ejecutada @ {data.get('avg_price')}", target=acc, details=data)
        elif state.lower() == "cancelled":
            self.audit.log("MANUAL_CANCEL", f"{acc}: {what} cancelada", target=acc, details=data)
        else:
            logger.debug(f"MANUAL_STATUS {acc} {what}: {state}")
