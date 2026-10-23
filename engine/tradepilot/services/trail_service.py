"""Trailing stop por posición, en tres modos:

- fijo: el stop sigue al mejor precio alcanzado a una distancia fija (ticks); solo se mueve a favor.
- adaptativo: la distancia es k × ATR (velas de 1 min, en ticks), acotada entre un mínimo y un máximo, y se estrecha un 25 %
  cuando el volumen del último minuto supera 1,5 veces la media (impulso: proteger más cerca). Sin velas suficientes usa
  la distancia fija.
- ia: cada AI_TRAIL_INTERVAL_S se le enseñan a Claude las últimas velas (OHLCV), ATR, volumen, estructura, entrada y stop
  actual, y propone un stop con su razón. El engine lo acota (nunca más lejos que el adaptativo, nunca más cerca que
  `min_ticks`, nunca en contra) y entre consultas aplica el adaptativo como red. Sin clave o si falla, actúa el adaptativo.

Reglas comunes: `activate_ticks` (no empieza hasta llevar ese beneficio), `step_ticks` (mínimo que debe mejorar para
mandar una modificación, evita saturar al bróker), `lock_be_ticks` (al llevar ese beneficio asegura breakeven + 1 tick).
Cada movimiento se audita (TRAIL_MOVE) y en la maestra las copias siguen vía ORDER_MODIFIED. Al desaparecer la posición
el trailing termina (TRAIL_END). El estado se guarda en SQLite y se retoma al reiniciar el engine.
"""
import asyncio
import json
import time
from datetime import datetime

from loguru import logger

from tradepilot.domain.symbols import point_value, tick_size

MODES = ("fixed", "adaptive", "ai")
KV_KEY = "trail.active"
AI_SYSTEM = (
    "Eres un gestor de riesgo de un trader intradía de futuros de EE. UU. Tu única tarea: decir dónde colocar el STOP de una "
    "posición abierta ahora mismo, usando las velas de 1 minuto, el ATR, el volumen y la estructura (máximos/mínimos recientes). "
    "Criterios: el stop va detrás de la estructura relevante (último mínimo de swing en un largo, último máximo en un corto) "
    "más un margen de ruido de ~0,5-1 ATR; en impulso con volumen alto, acércalo; en consolidación, dale aire pero nunca más "
    "de 3 ATR; nunca lo alejes del precio respecto al stop actual (solo se puede acercar o mantener). Responde ÚNICAMENTE un "
    'JSON: {"stop_price": número, "reason": "1 frase en español", "confidence": 0-1}.'
)


class Trail:
    def __init__(self, account: str, symbol: str, mode: str, **kw) -> None:
        self.account, self.symbol, self.mode = account, symbol, mode
        self.distance_ticks = int(kw.get("distance_ticks") or 40)
        self.step_ticks = max(1, int(kw.get("step_ticks") or 2))
        self.activate_ticks = int(kw.get("activate_ticks") or 0)
        self.lock_be_ticks = int(kw.get("lock_be_ticks") or 0)
        self.atr_k = float(kw.get("atr_k") or 1.5)
        self.min_ticks = max(1, int(kw.get("min_ticks") or 8))
        self.max_ticks = max(self.min_ticks, int(kw.get("max_ticks") or 200))
        self.best: float | None = kw.get("best")
        self.stop_price: float | None = kw.get("stop_price")
        self.stop_order_id: str | None = kw.get("stop_order_id")
        self.started_at = kw.get("started_at") or datetime.now().astimezone().isoformat(timespec="seconds")
        self.last_move_at: float | None = None
        self.last_ai_at: float | None = None
        self.ai_stop: float | None = None
        self.ai_reason: str = kw.get("ai_reason") or ""
        self.ai_confidence: float | None = None
        self.ai_error: str | None = None
        self.distance_now: float | None = None
        self.moves = int(kw.get("moves") or 0)
        self.active = False
        self.note = ""

    def to_dict(self) -> dict:
        return {"account": self.account, "symbol": self.symbol, "mode": self.mode, "distance_ticks": self.distance_ticks, "step_ticks": self.step_ticks,
                "activate_ticks": self.activate_ticks, "lock_be_ticks": self.lock_be_ticks, "atr_k": self.atr_k, "min_ticks": self.min_ticks, "max_ticks": self.max_ticks,
                "best": self.best, "stop_price": self.stop_price, "stop_order_id": self.stop_order_id, "started_at": self.started_at, "moves": self.moves,
                "active": self.active, "distance_now": self.distance_now, "ai_reason": self.ai_reason, "ai_confidence": self.ai_confidence, "ai_error": self.ai_error,
                "ai_stop": self.ai_stop, "note": self.note}


class TrailService:
    def __init__(self, trade, accounts, market, ai, audit, bridge, store=None, interval_s: float = 1.0, ai_interval_s: int = 60, ai_model: str = "") -> None:
        self.trade, self.accounts, self.market, self.ai, self.audit, self.bridge, self.store = trade, accounts, market, ai, audit, bridge, store
        self.interval_s, self.ai_interval_s, self.ai_model = interval_s, ai_interval_s, ai_model or None
        self.trails: dict[tuple[str, str], Trail] = {}
        self._task: asyncio.Task | None = None
        self._ai_inflight: set[tuple[str, str]] = set()
        self.stats = {"moves": 0, "ai_calls": 0, "ended": 0}
        if store is not None:
            try:
                for d in json.loads(store.get_kv(KV_KEY) or "[]"):
                    t = Trail(d["account"], d["symbol"], d.get("mode", "fixed"), **d)
                    self.trails[(t.account.lower(), t.symbol.lower())] = t
            except Exception as exc:
                logger.warning(f"No se pudo recuperar el trailing guardado: {exc}")

    # ---- ciclo de vida ----
    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(f"Trailing: {exc}")
            await asyncio.sleep(self.interval_s)

    def _save(self) -> None:
        if self.store is not None:
            try:
                self.store.set_kv(KV_KEY, json.dumps([t.to_dict() for t in self.trails.values()]))
            except Exception as exc:
                logger.warning(f"No se pudo guardar el trailing: {exc}")

    # ---- API ----
    def list(self) -> list[dict]:
        return [t.to_dict() for t in self.trails.values()]

    def get(self, account: str, symbol: str) -> Trail | None:
        return self.trails.get((account.lower(), symbol.lower()))

    async def start_trail(self, req: dict) -> dict:
        mode = str(req.get("mode") or "fixed")
        if mode not in MODES:
            raise ValueError("modo: fixed, adaptive o ai")
        if mode == "ai" and not self.ai.enabled:
            raise ValueError("el trailing IA necesita ANTHROPIC_API_KEY en engine/.env; usa el adaptativo mientras tanto")
        pv = self.trade.protect_preview(str(req.get("account", "")), str(req.get("symbol", "")))   # valida cuenta y posición
        acc, symbol = pv["account"], pv["symbol"]
        t = Trail(acc, symbol, mode, **{k: v for k, v in req.items() if k in ("distance_ticks", "step_ticks", "activate_ticks", "lock_be_ticks", "atr_k", "min_ticks", "max_ticks")})
        if t.distance_ticks <= 0:
            raise ValueError("distancia en ticks obligatoria")
        if pv["stops"]:
            t.stop_order_id = pv["stops"][0]["order_id"]
            t.stop_price = pv["stops"][0]["stop_price"]
        last = self.market.price(symbol)
        t.best = last if last else pv["avg_price"]
        self.trails[(acc.lower(), symbol.lower())] = t
        self._save()
        self.audit.log("TRAIL_START", f"{acc}: trailing {self._mode_es(t)} en {symbol} ({'largo' if pv['quantity'] > 0 else 'corto'} {abs(pv['quantity'])}) · "
                       f"{'distancia ' + str(t.distance_ticks) + ' ticks' if mode == 'fixed' else f'{t.atr_k} × ATR entre {t.min_ticks} y {t.max_ticks} ticks'}"
                       + (f" · activa a +{t.activate_ticks} ticks" if t.activate_ticks else "") + (f" · breakeven a +{t.lock_be_ticks} ticks" if t.lock_be_ticks else "")
                       + (" · stop actual " + f"{t.stop_price:.10g}" if t.stop_price else " · sin stop: se creará"), target=acc, details=t.to_dict())
        await self.tick()
        return t.to_dict()

    async def stop_trail(self, account: str, symbol: str, reason: str = "detenido desde la consola") -> dict | None:
        t = self.trails.pop((account.lower(), symbol.lower()), None)
        if t is None:
            return None
        self._save()
        self.stats["ended"] += 1
        self.audit.log("TRAIL_END", f"{t.account}: trailing en {t.symbol} terminado ({reason}) tras {t.moves} movimientos"
                       + (f"; el stop queda en {t.stop_price:.10g}" if t.stop_price else ""), target=t.account, details=t.to_dict())
        return t.to_dict()

    @staticmethod
    def _mode_es(t: Trail) -> str:
        return {"fixed": "fijo", "adaptive": "adaptativo", "ai": "IA"}[t.mode]

    # ---- motor ----
    async def tick(self) -> None:
        for key, t in list(self.trails.items()):
            try:
                await self._step(t)
            except Exception as exc:
                t.note = f"error: {str(exc)[:120]}"
                logger.warning(f"Trailing {t.account} {t.symbol}: {exc}")

    def _distance_ticks(self, t: Trail) -> tuple[float, str]:
        if t.mode == "fixed":
            return float(t.distance_ticks), f"fijo {t.distance_ticks} ticks"
        atr = self.market.atr_ticks(t.symbol)
        if atr is None:
            return float(t.distance_ticks), f"fijo {t.distance_ticks} ticks (sin velas suficientes para el ATR)"
        d = max(t.min_ticks, min(t.max_ticks, t.atr_k * atr))
        why = f"{t.atr_k} × ATR {atr:.0f} ticks"
        vr = self.market.volume_ratio(t.symbol)
        if vr is not None and vr >= 1.5:
            d = max(t.min_ticks, d * 0.75)
            why += f" · volumen ×{vr:.1f}: se acerca un 25 %"
        return round(d), why

    async def _step(self, t: Trail) -> None:
        snap = self.accounts.find(t.account)
        pos = next((p for p in snap.open_positions if p.symbol.lower() == t.symbol.lower() and p.quantity), None) if snap else None
        if pos is None:
            await self.stop_trail(t.account, t.symbol, "la posición ya no existe")
            return
        long, qty = pos.quantity > 0, abs(pos.quantity)
        tick = tick_size(t.symbol)
        last = self.market.price(t.symbol)
        if last is None:
            t.note = "sin precio en vivo: el addon no manda ticks de este instrumento"
            return
        # stop vivo según el bróker (puede haberlo movido otro: la maestra, el usuario, breakeven)
        stops, _ = self.trade._exit_orders(snap, pos.symbol, pos.quantity)
        cur = stops[0] if stops else None
        recent = t.last_move_at is not None and time.monotonic() - t.last_move_at < 4.0
        if cur is not None:
            t.stop_order_id = cur.order_id
            if not recent or t.stop_price is None:
                t.stop_price = cur.stop_price      # la instantánea del bróker tarda ~2 s en reflejar nuestro último movimiento
        elif not recent:
            t.stop_order_id = None
        t.best = max(t.best or last, last) if long else min(t.best or last, last)
        profit_ticks = ((last - pos.avg_price) if long else (pos.avg_price - last)) / tick
        t.active = t.activate_ticks <= 0 or profit_ticks >= t.activate_ticks
        if not t.active:
            t.note = f"esperando +{t.activate_ticks} ticks de beneficio (ahora {profit_ticks:+.0f})"
            return
        dist, why = self._distance_ticks(t)
        t.distance_now = dist
        cand = (t.best - dist * tick) if long else (t.best + dist * tick)
        reason = why
        if t.mode == "ai":
            await self._maybe_ask_ai(t, pos, last, dist)
            if t.ai_stop is not None:
                # la IA solo puede estar entre el adaptativo (más lejos) y min_ticks del precio (más cerca)
                floor_ = cand
                near = (last - t.min_ticks * tick) if long else (last + t.min_ticks * tick)
                ai = max(min(t.ai_stop, near), floor_) if long else min(max(t.ai_stop, near), floor_)
                if (long and ai > cand) or (not long and ai < cand):
                    cand, reason = ai, f"IA: {t.ai_reason or 'sin razón'}"
        if t.lock_be_ticks and profit_ticks >= t.lock_be_ticks:
            be = pos.avg_price + (tick if long else -tick)
            if (long and be > cand) or (not long and be < cand):
                cand, reason = be, f"breakeven asegurado (+{t.lock_be_ticks} ticks alcanzados)"
        cand = round(round(cand / tick) * tick, 8)
        # solo a favor, y solo si mejora al menos step_ticks (o no hay stop)
        if t.stop_price:
            improve = ((cand - t.stop_price) if long else (t.stop_price - cand)) / tick
            if improve < t.step_ticks:
                t.note = f"stop {t.stop_price:.10g} · siguiente a {cand:.10g} ({why})"
                return
        if (long and cand >= last) or (not long and cand <= last):
            t.note = "el stop calculado tocaría el precio: no se mueve"
            return
        if t.last_move_at is not None and time.monotonic() - t.last_move_at < 2.0:
            return
        t.last_move_at = time.monotonic()
        if cur is not None:
            await self.bridge.modify_order(t.account, cur.order_id, qty, 0.0, cand)
            action = "movido"
        else:
            order = self.trade.validate({"client_id": f"trail{int(time.time() * 1000)}", "account": t.account, "symbol": pos.symbol, "action": "SELL" if long else "BUY",
                                         "quantity": qty, "order_type": "STOPMARKET", "stop_price": cand, "closing": True, "note": "trailing"})
            reply = await self.bridge.manual_order(order)
            parts = reply.split("|")
            t.stop_order_id = parts[1] if len(parts) > 1 else None
            if t.stop_order_id:
                self.trade._exits[(t.account, t.stop_order_id)] = pos.symbol
            action = "creado"
        prev = t.stop_price
        t.stop_price = cand
        t.moves += 1
        self.stats["moves"] += 1
        self.trade.stats["modifies"] += 1
        self._save()
        pv = point_value(pos.symbol)
        locked = (cand - pos.avg_price) * (1 if long else -1) * pv * qty
        t.note = f"stop {action} a {cand:.10g} ({reason})"
        self.audit.log("TRAIL_MOVE", f"{t.account}: {pos.symbol} stop {action} a {cand:.10g}" + (f" (antes {prev:.10g})" if prev else "") +
                       f" · precio {last:.10g} · asegura {locked:+.0f} $ · {reason}", target=t.account,
                       details={"mode": t.mode, "stop": cand, "prev": prev, "last": last, "best": t.best, "distance_ticks": dist, "reason": reason})

    async def _maybe_ask_ai(self, t: Trail, pos, last: float, dist: float) -> None:
        key = (t.account.lower(), t.symbol.lower())
        if key in self._ai_inflight or (t.last_ai_at is not None and time.monotonic() - t.last_ai_at < self.ai_interval_s):
            return
        t.last_ai_at = time.monotonic()
        self._ai_inflight.add(key)
        try:
            long = pos.quantity > 0
            tick = tick_size(t.symbol)
            bars = [b.as_dict() for b in self.market.recent_bars(t.symbol, 30)]
            sw = self.market.swing(t.symbol, 10)
            atr = self.market.atr_ticks(t.symbol)
            vr = self.market.volume_ratio(t.symbol)
            stop_txt = f"{t.stop_price:.10g}" if t.stop_price else "ninguno"
            prompt = (f"Posición: {'LARGO' if long else 'CORTO'} {abs(pos.quantity)} {pos.symbol}, entrada {pos.avg_price:.10g}, precio actual {last:.10g}, tick {tick:g}.\n"
                      f"Stop actual: {stop_txt}. Distancia adaptativa de referencia: {dist:.0f} ticks "
                      f"(ATR 1 min: {f'{atr:.0f} ticks' if atr else 'desconocido'}; volumen último minuto / media: {f'{vr:.2f}' if vr else 'desconocido'}).\n")
            if sw:
                prompt += f"Máximo/mínimo de las últimas 10 velas: {sw[0]:.10g} / {sw[1]:.10g}.\n"
            prompt += "Velas de 1 minuto (más antigua primero), o/h/l/c/v:\n" + "\n".join(f"{b['t']} {b['o']:.10g} {b['h']:.10g} {b['l']:.10g} {b['c']:.10g} {b['v']}" for b in bars) + "\n¿Dónde va el stop ahora?"
            self.stats["ai_calls"] += 1
            r = await self.ai.ask(AI_SYSTEM, prompt, model=self.ai_model, max_tokens=800)
            sp = float(r.get("stop_price") or 0)
            if sp <= 0:
                raise ValueError("la IA no devolvió stop_price")
            t.ai_stop, t.ai_reason, t.ai_confidence, t.ai_error = round(round(sp / tick) * tick, 8), str(r.get("reason") or "")[:160], r.get("confidence"), None
        except Exception as exc:
            t.ai_error = str(exc)[:160]
            logger.warning(f"Trailing IA {t.account} {t.symbol}: {exc}")
        finally:
            self._ai_inflight.discard(key)
