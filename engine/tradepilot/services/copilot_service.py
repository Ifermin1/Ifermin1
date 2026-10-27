"""Copiloto en vivo: mientras hay posiciones abiertas, cada COPILOT_INTERVAL_S revisa cada posición con su contexto
(P&L flotante en R, stop y objetivo vivos, trailing, drawdown restante, noticias próximas, ATR, racha) y propone UNA acción
concreta por posición con el botón que la ejecuta: llevar a breakeven, apretar el stop, reducir a la mitad, cerrar antes
del dato, o nada que hacer. Con clave de IA la propuesta la hace Claude con ese contexto; sin clave, las reglas:

- sin stop                                   → proteger (stop a 1 ATR)
- noticia de impacto alto en ≤ 10 min        → cerrar o reducir antes del dato
- flotante ≥ +1 R y stop por debajo de entrada → breakeven
- flotante ≥ +2 R                             → apretar el stop a +1 R (o reducir a la mitad)
- drawdown restante < 2 × riesgo de la posición → reducir a la mitad
- 3 pérdidas seguidas hoy y posición en pérdida → cerrar y parar

Cada propuesta nueva (misma posición + misma acción) se audita una vez cada 30 min (COPILOT), así llega al teléfono.
No ejecuta nada por sí mismo: cada botón pasa por las mismas confirmaciones (y PIN) que el resto de la consola.
"""
import asyncio
import json
import time
from datetime import datetime

from loguru import logger

from tradepilot.domain.symbols import point_value, tick_size

COPILOT_SYSTEM = (
    "Eres el copiloto de un trader intradía de futuros con cuentas de prop firm. Te paso cada posición abierta con su contexto "
    "(flotante en $ y en R, stop y objetivo vivos, trailing, drawdown restante, límites, noticias próximas, ATR, racha de hoy). "
    "Para cada posición propón UNA acción de esta lista: breakeven (stop al precio de entrada), tighten (apretar el stop a un precio "
    "concreto), reduce (cerrar la mitad), close (cerrar toda), protect (crear stop si no hay) o hold (nada que hacer). Sé prudente: "
    "proteger beneficios y evitar noticias de impacto alto pesa más que optimizar. Español, breve, cifras concretas. Devuelve ÚNICAMENTE "
    'un JSON: {"items": [{"account": "...", "symbol": "...", "action": "breakeven|tighten|reduce|close|protect|hold", '
    '"price": número o null (solo para tighten/protect), "severity": "bad|warn|ok|info", "title": "≤ 60 caracteres", "detail": "1-2 frases"}]}'
)
ACTION_LABEL = {"breakeven": "Breakeven", "tighten": "Apretar stop", "reduce": "Cerrar la mitad", "close": "Cerrar", "protect": "Poner stop", "hold": ""}


class CopilotService:
    def __init__(self, accounts, risk, trade, market, news, ai, audit, store=None, trail=None, interval_s: int = 300, remind_s: int = 1800) -> None:
        self.accounts, self.risk, self.trade, self.market, self.news, self.ai, self.audit, self.store, self.trail = accounts, risk, trade, market, news, ai, audit, store, trail
        self.interval_s, self.remind_s = max(30, int(interval_s)), max(60, int(remind_s))
        self.enabled = True
        self.last: dict | None = None
        self._announced: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self.now = datetime.now
        if store is not None:
            self.enabled = (store.get_kv("copilot.enabled") or "1") == "1"

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        if self.store is not None:
            self.store.set_kv("copilot.enabled", "1" if on else "0")

    # ---- ciclo ----
    async def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                if self.enabled and self._positions():
                    await self.run("auto")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Copiloto: {exc}")
            await asyncio.sleep(self.interval_s)

    # ---- contexto ----
    def _positions(self) -> list[tuple]:
        out = []
        for a in self.accounts.accounts.values():
            if not a.enabled:
                continue
            for p in a.open_positions:
                if p.quantity:
                    out.append((a, p))
        return out

    def _streak(self, acc: str) -> int:
        day = self.now().strftime("%Y-%m-%d")
        trades = self.store.get_trades(day, day, acc) if self.store else []
        streak = 0
        for t in sorted(trades, key=lambda t: t.get("closed_at") or "", reverse=True):
            if float(t.get("pnl") or 0) - float(t.get("commissions") or 0) < 0:
                streak += 1
            else:
                break
        return streak

    def _context(self) -> list[dict]:
        items = []
        try:
            news = [{"title": u["event"]["title"], "in_minutes": u["minutes"], "impact": u["event"]["impact"]} for u in (self.news.alerts().get("upcoming", []) if self.news else [])]
        except Exception:
            news = []
        for a, p in self._positions():
            stops, tps = self.trade._exit_orders(a, p.symbol, p.quantity) if self.trade is not None else ([], [])
            tick, pv = tick_size(p.symbol), point_value(p.symbol) or 0
            last = self.market.price(p.symbol) if self.market is not None else None
            is_long = p.quantity > 0
            stop_price = stops[0].stop_price if stops else None
            risk_usd = abs(p.avg_price - stop_price) * pv * abs(p.quantity) if stop_price and pv else None
            float_usd = ((last - p.avg_price) if is_long else (p.avg_price - last)) * pv * abs(p.quantity) if last and pv else p.unrealized_pnl
            r_mult = (float_usd / risk_usd) if risk_usd and risk_usd > 0 else None
            lim = self.risk.limits.get(a.account_id)
            tr = self.trail.get(a.account_id, p.symbol) if self.trail is not None else None
            items.append({"account": a.account_id, "alias": a.alias, "symbol": p.symbol, "side": "long" if is_long else "short", "quantity": abs(p.quantity), "avg_price": p.avg_price, "last": last,
                          "float_usd": round(float_usd or 0, 2), "r_multiple": round(r_mult, 2) if r_mult is not None else None, "risk_usd": round(risk_usd, 2) if risk_usd else None,
                          "stop_price": stop_price, "target_price": tps[0].limit_price if tps else None, "trailing": tr.mode if tr is not None else None,
                          "drawdown_room_usd": a.drawdown.room, "drawdown_pct": a.drawdown.pct, "net_pnl_today": a.net_pnl,
                          "daily_loss_limit": lim.max_daily_loss if lim else None, "loss_streak_today": self._streak(a.account_id),
                          "atr_5m_ticks": self.market.atr_ticks(p.symbol, 5) if self.market is not None else None, "tick": tick, "point_value": pv, "news_next": news[:3]})
        return items

    # ---- reglas ----
    def _rules(self, items: list[dict]) -> list[dict]:
        out = []
        for it in items:
            acc, sym, long = it["account"], it["symbol"], it["side"] == "long"
            tick, atr, last = it["tick"], it["atr_5m_ticks"], it["last"]
            r, room, risk = it["r_multiple"], it["drawdown_room_usd"], it["risk_usd"]
            hi_news = [n for n in it["news_next"] if n["impact"] == "high" and n["in_minutes"] <= 10]
            mk = lambda action, sev, title, detail, price=None: {"account": acc, "symbol": sym, "action": action, "price": price, "severity": sev, "title": title, "detail": detail}  # noqa: E731
            if it["stop_price"] is None:
                dist = (atr or 40) * tick
                price = round((it["avg_price"] - dist) if long else (it["avg_price"] + dist), 8)
                out.append(mk("protect", "bad", "Sin stop: ponlo ahora", f"{abs(it['quantity'])} {sym} sin stop. Propuesta: stop a 1 ATR ({dist / tick:.0f} ticks) en {price:g}.", price))
                continue
            if hi_news:
                n = hi_news[0]
                out.append(mk("close" if (r is None or r < 1) else "reduce", "warn", f"{n['title']} en {n['in_minutes']} min", "Dato de impacto alto: el spread y el salto pueden saltar el stop. Cierra o reduce antes."))
                continue
            if it["loss_streak_today"] >= 3 and (it["float_usd"] or 0) < 0:
                out.append(mk("close", "warn", "3 pérdidas seguidas y esta va en contra", "Suele ser el momento de parar, no de aguantar."))
                continue
            if r is not None and r >= 2:
                price = round((it["avg_price"] + (it["risk_usd"] / (it["point_value"] * it["quantity"])) ) if long else (it["avg_price"] - (it["risk_usd"] / (it["point_value"] * it["quantity"]))), 8) if it["point_value"] else None
                out.append(mk("tighten", "ok", f"+{r:.1f} R: asegura +1 R", f"Flotante {it['float_usd']:+,.0f} $. Apretar el stop a +1 R ({price:g}) o cerrar la mitad.", price))
                continue
            if r is not None and r >= 1 and ((long and it["stop_price"] < it["avg_price"]) or (not long and it["stop_price"] > it["avg_price"])):
                out.append(mk("breakeven", "ok", f"+{r:.1f} R: lleva el stop a breakeven", f"Flotante {it['float_usd']:+,.0f} $ con el stop aún en pérdida ({it['stop_price']:g})."))
                continue
            if room is not None and risk and room < 2 * risk:
                out.append(mk("reduce", "warn", "El riesgo de esta posición es más de la mitad del drawdown que queda", f"Quedan {room:,.0f} $ y la posición arriesga {risk:,.0f} $."))
                continue
        return out

    # ---- ejecución ----
    async def run(self, reason: str = "manual") -> dict:
        items = self._context()
        suggestions: list[dict] = []
        source = "regla"
        model = None
        if items and self.ai is not None and self.ai.enabled:
            try:
                r = await self.ai.ask(COPILOT_SYSTEM, json.dumps({"positions": items, "now": self.now().strftime("%H:%M")}, ensure_ascii=False, default=str), max_tokens=900)
                model = r.get("model")
                known = {(i["account"].lower(), i["symbol"].lower()) for i in items}
                for x in r.get("items") or []:
                    if (str(x.get("account", "")).lower(), str(x.get("symbol", "")).lower()) in known and x.get("action") in ACTION_LABEL and x.get("action") != "hold":
                        suggestions.append({"account": x["account"], "symbol": x["symbol"], "action": x["action"], "price": x.get("price"), "severity": x.get("severity") if x.get("severity") in ("bad", "warn", "ok", "info") else "warn",
                                            "title": str(x.get("title") or ACTION_LABEL[x["action"]])[:80], "detail": str(x.get("detail") or "")[:300]})
                source = "ia"
            except Exception as exc:
                logger.warning(f"Copiloto: la IA falló, se usan las reglas: {exc}")
                suggestions = self._rules(items)
        else:
            suggestions = self._rules(items)
        for sg in suggestions:
            sg["label"] = ACTION_LABEL.get(sg["action"], sg["action"])
            sg["quantity"] = next((i["quantity"] for i in items if i["account"] == sg["account"] and i["symbol"] == sg["symbol"]), 0)
        self.last = {"ran_at": self.now().astimezone().isoformat(timespec="seconds"), "reason": reason, "source": source, "model": model, "positions": len(items), "suggestions": suggestions,
                     "enabled": self.enabled, "interval_s": self.interval_s}
        now = time.monotonic()
        for sg in suggestions:
            key = f"{sg['account']}|{sg['symbol']}|{sg['action']}"
            if now - self._announced.get(key, -1e9) >= self.remind_s:
                self._announced[key] = now
                self.audit.log("COPILOT", f"{sg['account']} {sg['symbol']}: {sg['title']} → {sg['label']}. {sg['detail']}", target=sg["account"], details=sg)
        return self.last

    def state(self) -> dict:
        return self.last or {"ran_at": None, "reason": None, "source": None, "model": None, "positions": len(self._positions()), "suggestions": [], "enabled": self.enabled, "interval_s": self.interval_s}
