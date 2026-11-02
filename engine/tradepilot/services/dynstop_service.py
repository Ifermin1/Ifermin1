"""Stop dinámico por cuenta: cada cuenta solo puede perder lo que le queda de SU ventana, no la de la maestra.

Cada 2 s (tras sincronizar), por cada cuenta activa con posición abierta:
1. **Ventana de pérdida que le queda** (en $, medida desde el precio medio de la posición): la menor entre
   - stop loss diario: `max_daily_loss + P&L neto realizado de hoy` (si tiene stop diario), y
   - drawdown: `margen hasta el suelo − flotante − colchón` (si tiene drawdown),
   menos un **margen de seguridad** (`margin_usd`, 10 $) para que el stop salte antes de tocar el límite.
2. **Pérdida al stop actual** = distancia del precio medio al stop × valor del punto × contratos.
3. Si la pérdida al stop supera la ventana, **acerca el stop** de esa cuenta al precio donde la pérdida = ventana (nunca lo
   aleja; nunca toca otras cuentas). Sin stop, lo crea. La maestra queda fuera salvo `include_master`.
4. Si la ventana ya no da ni para `min_ticks` de distancia al precio (ya no queda riesgo que proteger), **no lo toca** y
   lo deja anotado (DYNSTOP_NO_ROOM, una vez al día): el límite de riesgo cerrará la cuenta si llega.

Cuando la maestra mueve su stop, la copia de la seguidora cambia y, si vuelve a quedar fuera de su ventana, se corrige en
el siguiente ciclo. Convive con el trailing (los dos solo acercan). Auditoría: DYNSTOP_MOVE con de → a, ventana y pérdida.

**Corte diario con límite** (`profit_cut`): si la cuenta tiene ganancia máxima del día, el objetivo (orden LIMIT) de cada
posición se coloca donde la ganancia neta del día llegará EXACTAMENTE al corte (contando la comisión de cerrar): si el
objetivo copiado está más lejos, se acerca; si no hay, se crea. Así el corte se hace con una orden límite al precio justo
en vez de a mercado 2 s después (DYNCUT_MOVE). Si el precio ya pasó ese punto, el límite de riesgo cierra a mercado.
"""
import asyncio
import json
import time
from datetime import datetime

from loguru import logger

from tradepilot.domain.symbols import point_value, tick_size

DEFAULT = {"enabled": True, "margin_usd": 10.0, "include_master": False, "use_drawdown": True, "min_ticks": 4, "create_if_missing": True,
           "profit_cut": True, "profit_margin_usd": 0.0}


class DynStopService:
    def __init__(self, store, accounts, risk, trade, market, audit, bridge, commissions=None) -> None:
        self.store, self.accounts, self.risk, self.trade, self.market, self.audit, self.bridge = store, accounts, risk, trade, market, audit, bridge
        self.commissions = commissions
        self.now = datetime.now
        self.config = dict(DEFAULT)
        raw = store.get_kv("dynstop.config") if store else None
        if raw:
            try:
                self.config.update({k: v for k, v in json.loads(raw).items() if k in self.config})
            except ValueError:
                pass
        self.last: dict[str, dict] = {}            # cuenta -> última acción/estado para la consola
        self._applied: dict[tuple, tuple[float, float]] = {}   # (cuenta, order_id) -> (stop puesto, monotonic)
        self._noted: dict[str, str] = {}           # cuenta -> día del aviso "sin ventana"
        self.stats = {"moves": 0, "created": 0, "cuts": 0}

    def set_config(self, **changes) -> dict:
        for k, v in changes.items():
            if k in self.config and v is not None:
                self.config[k] = type(DEFAULT[k])(v)
        if self.store:
            self.store.set_kv("dynstop.config", json.dumps(self.config))
        return self.config

    # ---- ventana de pérdida por cuenta (desde el precio medio, en $) ----
    def room_for(self, snap) -> tuple[float | None, str]:
        lim = self.risk.limits.get(snap.account_id) if self.risk is not None else None
        if lim is None:
            return None, "sin límites"
        realized = (snap.net_pnl or 0.0) - (snap.unrealized_pnl or 0.0)
        rooms = []
        if lim.max_daily_loss > 0:
            rooms.append((lim.max_daily_loss + realized, "stop loss diario"))
        if self.config["use_drawdown"] and lim.max_trailing_drawdown > 0 and snap.drawdown.room is not None:
            rooms.append((snap.drawdown.room - (snap.unrealized_pnl or 0.0) - float(lim.drawdown_buffer or 0), "drawdown"))
        if not rooms:
            return None, "sin stop diario ni drawdown"
        room, why = min(rooms, key=lambda r: r[0])
        return round(room - float(self.config["margin_usd"]), 2), why

    def _last_price(self, pos) -> float | None:
        px = self.market.price(pos.symbol) if self.market is not None else None
        if px:
            return px
        pv = point_value(pos.symbol)
        if pv and pos.quantity and pos.unrealized_pnl is not None:
            return pos.avg_price + pos.unrealized_pnl / (pv * pos.quantity)
        return None

    # ---- ciclo ----
    async def tick(self) -> None:
        if not self.config["enabled"] or self.accounts is None or self.bridge is None:
            return
        master = (self.bridge.health.master_account or "").lower() if getattr(self.bridge, "health", None) else ""
        for snap in list(self.accounts.accounts.values()):
            if not snap.enabled or not snap.open_positions:
                continue
            if master and snap.account_id.lower() == master and not self.config["include_master"]:
                continue
            try:
                await self._account(snap)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Stop dinámico {snap.account_id}: {exc}")

    def _close_fee(self, symbol: str, qty: int) -> float:
        c = self.commissions
        if c is None or not getattr(c.config, "enabled", False):
            return 0.0
        try:
            return float(c.rate(symbol)) * qty
        except Exception:
            return 0.0

    async def _profit_cut(self, snap, pos, today: str) -> None:
        """Objetivo límite donde la ganancia neta del día = corte (ganancia máxima del día de la cuenta)."""
        lim = self.risk.limits.get(snap.account_id) if self.risk is not None else None
        if lim is None or lim.max_daily_profit <= 0 or lim.trading_halted:
            return
        long, qty = pos.quantity > 0, abs(pos.quantity)
        pv, tick = point_value(pos.symbol), tick_size(pos.symbol)
        realized = (snap.net_pnl or 0.0) - (snap.unrealized_pnl or 0.0)
        need = lim.max_daily_profit - realized + self._close_fee(pos.symbol, qty) + float(self.config["profit_margin_usd"] or 0)
        entry = self.last.setdefault(snap.account_id, {"at": self.now().isoformat(timespec="seconds"), "room": None, "note": ""})
        if need <= 0:
            entry["cut"] = {"target": None, "note": "el corte ya está alcanzado: lo cierra el límite de riesgo"}
            return
        cand = pos.avg_price + need / (pv * qty) if long else pos.avg_price - need / (pv * qty)
        cand = round(round(cand / tick) * tick, 8)
        _, tps = self.trade._exit_orders(snap, pos.symbol, pos.quantity)
        cur = tps[0] if tps else None
        entry["cut"] = {"target": cand, "current": cur.limit_price if cur else None, "need": round(need, 2), "note": ""}
        if cur is not None and cur.limit_price and ((long and cur.limit_price <= cand) or (not long and cur.limit_price >= cand)):
            entry["cut"]["note"] = "el objetivo ya está en el corte o más cerca"
            return
        last = self._last_price(pos)
        if last is not None and ((long and cand <= last) or (not long and cand >= last)):
            entry["cut"]["note"] = "el precio ya pasó el corte: cierra el límite de riesgo a mercado"
            return
        key = (snap.account_id, "cut:" + (cur.order_id if cur else "new"))
        prev = self._applied.get(key)
        if prev and abs(prev[0] - cand) < tick / 2 and time.monotonic() - prev[1] < 10:
            return
        self._applied[key] = (cand, time.monotonic())
        if cur is not None:
            await self.bridge.modify_order(snap.account_id, cur.order_id, cur.quantity or qty, cand, 0.0)
            how = f"objetivo movido de {cur.limit_price:.10g} a {cand:.10g}"
        elif self.config["create_if_missing"]:
            order = self.trade.validate({"client_id": f"cut{int(time.time() * 1000)}", "account": snap.account_id, "symbol": pos.symbol, "action": "SELL" if long else "BUY",
                                         "quantity": qty, "order_type": "LIMIT", "limit_price": cand, "closing": True, "note": "corte diario"})
            reply = await self.bridge.manual_order(order)
            parts = reply.split("|")
            if len(parts) > 1 and parts[1]:
                self.trade._exits[(snap.account_id, parts[1])] = pos.symbol
            how = f"sin objetivo: creado límite en {cand:.10g}"
        else:
            return
        self.stats["cuts"] += 1
        entry["cut"]["note"] = how
        self.audit.log("DYNCUT_MOVE", f"{snap.account_id} {pos.symbol}: {how} · ahí la ganancia neta del día llega justo al corte de {lim.max_daily_profit:,.0f} $ (faltan {need:,.0f} $ con la comisión de cerrar)",
                       target=snap.account_id, details={"symbol": pos.symbol, "from": cur.limit_price if cur else None, "to": cand, "need": need, "cut": lim.max_daily_profit, "qty": pos.quantity})

    async def _account(self, snap) -> None:
        today = self.now().strftime("%Y-%m-%d")
        if self.config["profit_cut"]:
            for pos in snap.open_positions:
                if pos.quantity:
                    await self._profit_cut(snap, pos, today)
        room, why = self.room_for(snap)
        if room is None:
            self.last.setdefault(snap.account_id, {})
            self.last[snap.account_id].update({"at": self.now().isoformat(timespec="seconds"), "room": None, "note": why})
            return
        for pos in snap.open_positions:
            if not pos.quantity:
                continue
            long, qty = pos.quantity > 0, abs(pos.quantity)
            pv, tick = point_value(pos.symbol), tick_size(pos.symbol)
            if not pv or not tick:
                continue
            stops, _ = self.trade._exit_orders(snap, pos.symbol, pos.quantity)
            cur = stops[0] if stops else None
            loss_now = ((pos.avg_price - cur.stop_price) if long else (cur.stop_price - pos.avg_price)) * pv * qty if cur and cur.stop_price else None
            self.last.setdefault(snap.account_id, {})
            self.last[snap.account_id].update({"at": self.now().isoformat(timespec="seconds"), "symbol": pos.symbol, "room": room, "limited_by": why,
                                               "stop": cur.stop_price if cur else None, "loss_at_stop": round(loss_now, 2) if loss_now is not None else None, "note": ""})
            if loss_now is not None and loss_now <= room:
                self.last[snap.account_id]["note"] = "el stop ya está dentro de la ventana"
                continue
            if room <= 0:
                self._no_room(snap, pos, room, why, today, "la ventana ya está agotada")
                continue
            dist = room / (pv * qty)                              # puntos desde el precio medio
            cand = pos.avg_price - dist if long else pos.avg_price + dist
            cand = round(round(cand / tick) * tick, 8)
            last = self._last_price(pos)
            if last is not None and ((long and cand >= last - self.config["min_ticks"] * tick) or (not long and cand <= last + self.config["min_ticks"] * tick)):
                self._no_room(snap, pos, room, why, today, f"el stop necesario ({cand:.10g}) quedaría a menos de {self.config['min_ticks']} ticks del precio ({last:.10g})")
                continue
            if cur is not None and cur.stop_price and ((long and cur.stop_price >= cand) or (not long and cur.stop_price <= cand)):
                continue        # ya está más cerca (lo movió el trailing, la maestra o este servicio hace un momento)
            key = (snap.account_id, cur.order_id if cur else "new")
            prev = self._applied.get(key)
            if prev and abs(prev[0] - cand) < tick / 2 and time.monotonic() - prev[1] < 10:
                continue        # ya pedido: la instantánea del bróker tarda ~2 s en reflejarlo
            self._applied[key] = (cand, time.monotonic())
            if cur is not None:
                await self.bridge.modify_order(snap.account_id, cur.order_id, cur.quantity or qty, 0.0, cand)
                self.stats["moves"] += 1
                how = f"stop movido de {cur.stop_price:.10g} a {cand:.10g}"
            elif self.config["create_if_missing"]:
                order = self.trade.validate({"client_id": f"dyn{int(time.time() * 1000)}", "account": snap.account_id, "symbol": pos.symbol, "action": "SELL" if long else "BUY",
                                             "quantity": qty, "order_type": "STOPMARKET", "stop_price": cand, "closing": True, "note": "stop dinámico"})
                reply = await self.bridge.manual_order(order)
                parts = reply.split("|")
                if len(parts) > 1 and parts[1]:
                    self.trade._exits[(snap.account_id, parts[1])] = pos.symbol
                self.stats["created"] += 1
                how = f"sin stop: creado en {cand:.10g}"
            else:
                continue
            self.last[snap.account_id].update({"stop": cand, "loss_at_stop": round(room, 2), "note": how})
            self.audit.log("DYNSTOP_MOVE", f"{snap.account_id} {pos.symbol}: {how} · pérdida al stop {room:,.0f} $ = lo que le queda a esta cuenta por {why} (menos {self.config['margin_usd']:,.0f} $ de margen)"
                           + (f"; con el stop copiado perdía {loss_now:,.0f} $" if loss_now is not None else ""),
                           target=snap.account_id, details={"symbol": pos.symbol, "from": cur.stop_price if cur else None, "to": cand, "room": room, "limited_by": why, "qty": pos.quantity})

    def _no_room(self, snap, pos, room, why, today, reason) -> None:
        self.last[snap.account_id]["note"] = f"sin ventana que proteger: {reason}; se deja como está"
        if self._noted.get(snap.account_id) != today:
            self._noted[snap.account_id] = today
            self.audit.log("DYNSTOP_NO_ROOM", f"{snap.account_id} {pos.symbol}: {reason} (ventana {room:,.0f} $ por {why}). No se toca el stop: si llega, el límite de riesgo cierra la cuenta.",
                           target=snap.account_id, details={"room": room, "limited_by": why})

    def state(self) -> dict:
        return {"config": self.config, "stats": self.stats, "accounts": self.last}
