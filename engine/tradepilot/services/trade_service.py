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

from tradepilot.domain.symbols import point_value, tick_size

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
        self.stats = {"orders": 0, "rejected": 0, "cancels": 0, "modifies": 0, "unconfirmed": 0, "protects": 0, "orphans": 0}
        self._exits: dict[tuple[str, str], str] = {}
        self._cancelled_by_us: OrderedDict[str, float] = OrderedDict()   # orderKey -> cuándo: para no auditar dos veces la cancelación     # (cuenta, orderKey) -> símbolo: salidas creadas por Proteger, para cancelarlas si la posición ya cerró

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
        if not req.get("closing"):
            ok, why = self.risk.allows(account, qty, symbol, action)
            if not ok:
                raise ValueError(f"límite de riesgo: {why}")
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
        out = {"id": cid, "account": account, "symbol": symbol, "action": action, "quantity": qty, "order_type": otype,
               "limit_price": limit if otype in ("LIMIT", "STOPLIMIT") else 0.0, "stop_price": stop if otype in ("STOPMARKET", "STOPLIMIT") else 0.0,
               "sl_ticks": sl, "tp_ticks": tp, "tick": tick_size(symbol), "note": str(req.get("note") or "")[:80]}
        if req.get("oco"):
            out["oco"] = str(req["oco"])[:48]        # addon >= 2.8: las dos salidas de Proteger se enlazan (una ejecuta, la otra se cancela)
        return out

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
        price = f" @ {order['limit_price']:.10g}" if order["limit_price"] else (f" stop {order['stop_price']:.10g}" if order["stop_price"] else "")
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
        self._note_cancel(order_id)
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
        result = await self.place({"client_id": client_id, "account": snap.account_id, "symbol": pos.symbol, "action": "SELL" if pos.quantity > 0 else "BUY",
                                   "quantity": abs(pos.quantity), "order_type": "MARKET", "closing": True, "note": "cierre de posición desde la consola"})
        # sin posición, un stop o un objetivo vivos serían una ENTRADA nueva: se cancelan ya (en la maestra, las copias siguen)
        stops, tps = self._exit_orders(snap, pos.symbol, pos.quantity)
        cancelled = []
        for o in stops + tps:
            try:
                await self.bridge.cancel_order(snap.account_id, o.order_id)
                self._note_cancel(o.order_id)
                cancelled.append(f"{o.order_type} {o.quantity} @ {(o.stop_price or o.limit_price):.10g}")
            except Exception as exc:
                logger.warning(f"Cierre de {snap.account_id} {pos.symbol}: no se pudo cancelar {o.order_id}: {exc}")
        if cancelled:
            self.audit.log("MANUAL_CANCEL", f"{snap.account_id}: tras cerrar {pos.symbol} se cancelan sus salidas: " + ", ".join(cancelled), target=snap.account_id,
                           details={"order_ids": [o.order_id for o in stops + tps]})
        result["cancelled_exits"] = len(cancelled)
        return result

    # ---- proteger una posición: stop a breakeven, stop y objetivo rápidos ----
    @staticmethod
    def _exit_orders(snap, symbol: str, qty: int) -> tuple[list, list]:
        """Órdenes vivas que cierran esa posición: stops (StopMarket/StopLimit) y objetivos (Limit) del lado contrario."""
        exit_action = "SELL" if qty > 0 else "BUY"
        own = [o for o in snap.working_orders if o.symbol.lower() == symbol.lower() and o.action.upper().startswith(exit_action) and o.state.lower() not in ("filled", "cancelled", "rejected")]
        return [o for o in own if o.order_type.upper() in ("STOPMARKET", "STOPLIMIT")], [o for o in own if o.order_type.upper() == "LIMIT"]

    def protect_preview(self, account: str, symbol: str) -> dict:
        snap = self.accounts.find(account)
        if snap is None:
            raise ValueError(f"cuenta desconocida: {account}")
        pos = next((p for p in snap.open_positions if p.symbol.lower() == symbol.lower() and p.quantity), None)
        if pos is None:
            raise ValueError(f"{snap.account_id} no tiene posición en {symbol}")
        stops, tps = self._exit_orders(snap, pos.symbol, pos.quantity)
        return {"account": snap.account_id, "symbol": pos.symbol, "quantity": pos.quantity, "avg_price": pos.avg_price, "tick": tick_size(pos.symbol), "point_value": point_value(pos.symbol),
                "stops": [o.model_dump() for o in stops], "targets": [o.model_dump() for o in tps]}

    async def protect(self, req: dict) -> dict:
        """Mueve (o crea) el stop y/o el objetivo de una posición. `breakeven` pone el stop en el precio medio ± `be_offset_ticks`;
        si no, `sl_price` / `tp_price` (0 = no tocar). Con `last_price` el engine comprueba que el stop no se dispare al instante
        (salvo `force`). Si existen órdenes de salida se modifican (en la maestra, las copias siguen); si no, se crean, y si
        se crean las dos a la vez van enlazadas en OCO (addon >= 2.8)."""
        self._check_addon()
        acc = self._check_account(str(req.get("account", "")), closing=True)
        snap = self.accounts.find(acc)
        symbol = str(req.get("symbol", ""))
        pos = next((p for p in snap.open_positions if p.symbol.lower() == symbol.lower() and p.quantity), None)
        if pos is None:
            raise ValueError(f"{acc} no tiene posición en {symbol}")
        qty, long = abs(pos.quantity), pos.quantity > 0
        tick = tick_size(pos.symbol)
        rnd = lambda x: round(round(x / tick) * tick, 8)  # noqa: E731
        sl_price = float(req.get("sl_price") or 0)
        if req.get("breakeven"):
            off = int(req.get("be_offset_ticks") or 0)
            sl_price = rnd(pos.avg_price + (off if long else -off) * tick)
        tp_price = float(req.get("tp_price") or 0)
        if sl_price <= 0 and tp_price <= 0:
            raise ValueError("indica stop, objetivo o breakeven")
        last = float(req.get("last_price") or 0)
        if sl_price > 0 and last > 0 and not req.get("force") and ((long and sl_price >= last) or (not long and sl_price <= last)):
            raise ValueError(f"el stop en {sl_price:.10g} se dispararía al instante (último {last:.10g}); si quieres cerrar, usa Cerrar o confirma con force")
        if tp_price > 0 and last > 0 and not req.get("force") and ((long and tp_price <= last) or (not long and tp_price >= last)):
            raise ValueError(f"el objetivo en {tp_price:.10g} se ejecutaría al instante (último {last:.10g})")
        if sl_price > 0 and tp_price > 0 and ((long and sl_price >= tp_price) or (not long and sl_price <= tp_price)):
            raise ValueError("el stop y el objetivo están del mismo lado")
        stops, tps = self._exit_orders(snap, pos.symbol, pos.quantity)
        exit_action = "SELL" if long else "BUY"
        cid = str(req.get("client_id") or "")
        both_new = sl_price > 0 and tp_price > 0 and not stops and not tps
        oco = f"TPM-OCO-{cid}" if both_new and cid else ""
        t0 = time.monotonic()
        out: dict = {"account": acc, "symbol": pos.symbol, "quantity": pos.quantity, "avg_price": pos.avg_price, "sl": None, "tp": None}
        for kind, price, existing in (("sl", rnd(sl_price) if sl_price > 0 else 0, stops), ("tp", rnd(tp_price) if tp_price > 0 else 0, tps)):
            if price <= 0:
                continue
            if existing:
                o = existing[0]
                cur = o.stop_price if kind == "sl" else o.limit_price
                if abs(cur - price) < tick / 2 and o.quantity == qty:
                    out[kind] = {"action": "unchanged", "order_id": o.order_id, "price": price}
                    continue
                reply = await self.bridge.modify_order(acc, o.order_id, qty, 0.0 if kind == "sl" else price, price if kind == "sl" else 0.0)
                self.stats["modifies"] += 1
                out[kind] = {"action": "modified", "order_id": o.order_id, "price": price, "from": cur, "detail": reply}
                for extra in existing[1:]:   # más de un stop/objetivo para la misma posición: se igualan también
                    try:
                        await self.bridge.modify_order(acc, extra.order_id, extra.quantity, 0.0 if kind == "sl" else price, price if kind == "sl" else 0.0)
                    except Exception as exc:
                        logger.warning(f"Proteger {acc} {pos.symbol}: no se pudo mover {extra.order_id}: {exc}")
            else:
                order = self.validate({"client_id": f"{cid}-{kind}" if cid else f"p{int(time.time() * 1000)}-{kind}", "account": acc, "symbol": pos.symbol, "action": exit_action,
                                       "quantity": qty, "order_type": "STOPMARKET" if kind == "sl" else "LIMIT", "stop_price": price if kind == "sl" else 0,
                                       "limit_price": price if kind == "tp" else 0, "closing": True, "oco": oco, "note": "proteger"})
                reply = await self.bridge.manual_order(order)
                parts = reply.split("|")
                oid = parts[1] if len(parts) > 1 else ""
                if oid:
                    self._exits[(acc, oid)] = pos.symbol
                self.stats["orders"] += 1
                out[kind] = {"action": "created", "order_id": oid, "price": price, "detail": reply}
        ms = round((time.monotonic() - t0) * 1000)
        out["ms"] = ms
        self.bridge.health.last_order_ms = ms
        self.stats["protects"] += 1
        pv = point_value(pos.symbol)
        ES = {"created": "creado", "modified": "movido", "unchanged": "sin cambio"}
        txt = []
        if out["sl"]:
            txt.append(f"stop {ES[out['sl']['action']]} en {out['sl']['price']:.10g}" + (" (breakeven)" if req.get("breakeven") else "") + f" = {(round((out['sl']['price'] - pos.avg_price) * (1 if long else -1) * pv) or 0):+.0f} $/contrato")
        if out["tp"]:
            txt.append(f"objetivo {ES[out['tp']['action']]} en {out['tp']['price']:.10g} = {((out['tp']['price'] - pos.avg_price) * (1 if long else -1) * pv):+.0f} $/contrato")
        master = (self.bridge.health.master_account or "").lower() == acc.lower()
        self.audit.log("MANUAL_PROTECT", f"{acc}: {'largo' if long else 'corto'} {qty} {pos.symbol} @ {pos.avg_price:.10g}: " + "; ".join(txt) + f" ({ms} ms)"
                       + (" · es la maestra: las copias siguen" if master else ""), target=acc, details={"request": req, "result": out})
        return out

    async def sweep_orphans(self) -> None:
        """Tras cada sincronización: una salida creada por Proteger cuya posición ya no existe se cancela (si el addon no la
        enlazó en OCO). Así un objetivo no se convierte en una entrada nueva cuando el stop ya cerró la posición."""
        if not self._exits:
            return
        for (acc, oid), symbol in list(self._exits.items()):
            snap = self.accounts.find(acc)
            if snap is None:
                self._exits.pop((acc, oid), None)
                continue
            alive = next((o for o in snap.working_orders if o.order_id == oid), None)
            if alive is None:
                self._exits.pop((acc, oid), None)
                continue
            if any(p.symbol.lower() == symbol.lower() and p.quantity for p in snap.open_positions):
                continue
            self._exits.pop((acc, oid), None)
            try:
                await self.bridge.cancel_order(acc, oid)
                self._note_cancel(oid)
                self.stats["orphans"] += 1
                self.audit.log("MANUAL_CANCEL", f"{acc}: {alive.action} {alive.quantity} {symbol} {alive.order_type} cancelada: la posición ya está cerrada (salida huérfana)",
                               target=acc, details={"order_id": oid})
            except Exception as exc:
                logger.warning(f"No se pudo cancelar la salida huérfana {oid} de {acc}: {exc}")

    def _note_cancel(self, order_id: str) -> None:
        self._cancelled_by_us[order_id] = time.monotonic()
        while len(self._cancelled_by_us) > 200:
            self._cancelled_by_us.popitem(last=False)

    def on_manual_status(self, data: dict) -> None:
        """MANUAL_STATUS del addon (v2.7): ciclo de vida de una orden manual en cualquier cuenta (aceptada, ejecutada, rechazada)."""
        state = str(data.get("state") or "")
        acc = str(data.get("account") or "")
        err = str(data.get("error") or "")
        what = f"{data.get('action')} {data.get('quantity')} {data.get('symbol')} {data.get('order_type')}"
        mid, oid = str(data.get("manual_id") or ""), str(data.get("order_id") or "")
        if oid and (mid.endswith("-SL") or mid.endswith("-TP")) and state.lower() in ("accepted", "working"):
            self._exits[(acc, oid)] = str(data.get("symbol") or "")    # stop/objetivo automáticos del addon: se cancelan si la posición desaparece
        if err or state.lower() == "rejected":
            self.stats["rejected"] += 1
            self.audit.log("MANUAL_REJECTED", f"{acc}: {what} rechazada por el bróker: {err or state} {data.get('native_error') or ''}".strip(),
                           target=acc, details=data)
        elif state.lower() == "filled":
            self.audit.log("MANUAL_FILL", f"{acc}: {what} ejecutada @ {data.get('avg_price')}", target=acc, details=data)
        elif state.lower() == "cancelled":
            if str(data.get("order_id") or "") in self._cancelled_by_us:
                return    # ya auditada al pedirla desde la consola (o al cancelar una salida huérfana)
            self.audit.log("MANUAL_CANCEL", f"{acc}: {what} cancelada (OCO, NinjaTrader o a mano)", target=acc, details=data)
        else:
            logger.debug(f"MANUAL_STATUS {acc} {what}: {state}")
