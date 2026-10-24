"""Coach: la IA que trabaja contigo sobre tus propios datos.

- review(day): revisión de la sesión. Reúne las operaciones del día, el P&L por cuenta, los avisos de la auditoría, las
  noticias del día y estadísticas; Claude señala los patrones que cuestan dinero y qué cambiar mañana. Sin clave, una
  revisión por reglas (stops saltados en menos de 60 s, entradas justo después de una noticia, rachas, tamaño…).
  Se guarda por día (kv) para no pagarla dos veces.
- check_entry(order): semáforo antes de entrar. Reglas rápidas (kill switch, día cerrado, drawdown restante, pérdida diaria,
  racha de hoy, noticia en ±10 min, volatilidad, tamaño frente al tope) → verde / ámbar / rojo con los motivos. Con `ai`
  pide además una frase a Claude.
- ask(question): chat con tus datos. Resume 90 días (por día de la semana, hora, símbolo, cuenta, lado, mejores y peores)
  y las últimas operaciones, y Claude responde en español citando los números.
"""
import json
import time
from datetime import datetime, timedelta

from loguru import logger

from tradepilot.domain import tradestats as ts
from tradepilot.domain.symbols import tick_size

REVIEW_SYSTEM = (
    "Eres el coach de un trader intradía de futuros que copia sus operaciones a varias cuentas de prop firm. Te paso los datos "
    "de una sesión: operaciones, P&L por cuenta, avisos del sistema, noticias y estadísticas. Sé concreto y útil, en español, "
    "sin frases hechas. Devuelve ÚNICAMENTE un JSON con: "
    '"summary" (3-4 frases con los números clave), '
    '"patterns" (lista de 2-5 objetos {"title","evidence","cost_usd"}: patrones que costaron dinero o lo ganaron, con la prueba en los datos), '
    '"tomorrow" (lista de 3 reglas concretas y medibles para la próxima sesión), '
    '"score" (0-10 de disciplina, no de resultado), "mood" ("bien"|"regular"|"mal").'
)
ASK_SYSTEM = (
    "Eres un analista que responde preguntas de un trader sobre SU historial de operaciones. Usa solo los datos que te paso, "
    "cita cifras concretas (número de operaciones, neto, acierto) y si los datos no bastan dilo. Español, directo, máximo 8 frases. "
    'Devuelve ÚNICAMENTE un JSON: {"answer": "...", "numbers": ["dato 1", "dato 2"]}'
)
CHECK_SYSTEM = (
    "Eres un gestor de riesgo. Te paso una orden que un trader va a mandar y su contexto (riesgo, racha, noticias, volatilidad). "
    'Devuelve ÚNICAMENTE un JSON: {"verdict": "verde"|"ámbar"|"rojo", "reason": "1 frase en español"}'
)


class CoachService:
    def __init__(self, store, accounts, risk, news, market, ai, trade=None) -> None:
        self.store, self.accounts, self.risk, self.news, self.market, self.ai, self.trade = store, accounts, risk, news, market, ai, trade
        self.now = datetime.now

    # ---- revisión de la sesión ----
    def _day_data(self, day: str) -> dict:
        trades = self.store.get_trades(day, day) if self.store else []
        audits = [a for a in self.store.get_recent_audits(500) if a.timestamp.strftime("%Y-%m-%d") == day] if self.store else []
        counts: dict[str, int] = {}
        for a in audits:
            counts[a.event_type] = counts.get(a.event_type, 0) + 1
        news = [e for e in (self.store.get_news_events(day + "T00:00:00", day + "T23:59:59") if self.store else []) if e.get("impact") in ("medium", "high")]
        near = []
        for t in trades:
            try:
                o = datetime.fromisoformat(t.get("opened_at") or t["closed_at"])
            except (TypeError, ValueError):
                continue
            for e in news:
                try:
                    et = datetime.fromisoformat(e["time"]).replace(tzinfo=None) if "+" in e["time"] or "Z" in e["time"] else datetime.fromisoformat(e["time"])
                    et = datetime.fromisoformat(e["time"]).astimezone().replace(tzinfo=None)
                except (TypeError, ValueError):
                    continue
                dmin = (o - et).total_seconds() / 60
                if -5 <= dmin <= 15:
                    near.append({"trade": ts.brief(t), "net": ts.net_of(t), "event": e["title"], "minutes_after": round(dmin)})
        quick_stops = [t for t in trades if ts.net_of(t) < 0 and (ts.seconds_of(t) or 1e9) < 60]
        stats = ts.trade_stats(trades)
        return {"day": day, "trades": trades, "stats": stats, "by_hour": ts.by_hour(trades), "by_account": ts.by_account(trades), "by_symbol": ts.by_symbol(trades),
                "by_side": ts.by_side(trades), "worst": [ts.brief(t) for t in ts.worst(trades)], "best": [ts.brief(t) for t in ts.best(trades)],
                "audit_counts": counts, "news": [{"time": e["time"][11:16], "title": e["title"], "impact": e["impact"], "actual": e.get("actual"), "forecast": e.get("forecast")} for e in news],
                "near_news": near, "quick_stops": [ts.brief(t) for t in quick_stops],
                "rejections": [a.message[:160] for a in audits if a.event_type in ("MANUAL_REJECTED", "FOLLOWER_REJECTED")][:8],
                "day_closed": counts.get("DAY_CLOSED", 0) > 0}

    def heuristic_rules(self, trades: list[dict]) -> list[str]:
        """Las reglas 'para mañana' por reglas a partir de un conjunto de operaciones (el informe semanal las usa)."""
        quick = [t for t in trades if ts.net_of(t) < 0 and (ts.seconds_of(t) or 1e9) < 60]
        d = {"day": "periodo", "trades": trades, "stats": ts.trade_stats(trades), "by_hour": ts.by_hour(trades), "worst": [ts.brief(t) for t in ts.worst(trades)], "best": [ts.brief(t) for t in ts.best(trades)],
             "quick_stops": [ts.brief(t) for t in quick], "near_news": [], "rejections": [], "day_closed": False}
        return self._heuristic_review(d)["tomorrow"]

    def _heuristic_review(self, d: dict) -> dict:
        s, patterns, tomorrow = d["stats"], [], []
        if s["n"] == 0:
            return {"summary": f"Sin operaciones cerradas el {d['day']}.", "patterns": [], "tomorrow": ["Si no operas, que sea por decisión: anota el motivo."], "score": 10, "mood": "bien"}
        if d["quick_stops"]:
            cost = sum(ts.net_of(t) for t in d["trades"] if ts.net_of(t) < 0 and (ts.seconds_of(t) or 1e9) < 60)
            patterns.append({"title": f"{len(d['quick_stops'])} stops saltaron en menos de 60 s", "evidence": "; ".join(d["quick_stops"][:4]), "cost_usd": round(cost)})
            tomorrow.append("Stop mínimo de 15-20 puntos en NQ (o no entrar) y tamaño que lo permita.")
        if d["near_news"]:
            cost = sum(x["net"] for x in d["near_news"] if x["net"] < 0)
            patterns.append({"title": f"{len(d['near_news'])} entradas en los 15 min siguientes a una noticia", "evidence": "; ".join(f"{x['trade']} tras {x['event']} (+{x['minutes_after']} min)" for x in d["near_news"][:3]), "cost_usd": round(cost)})
            tomorrow.append("Nada de entradas desde 5 min antes hasta 15 min después de un dato de impacto medio o alto.")
        if s["max_loss_streak"] >= 3:
            patterns.append({"title": f"Racha de {s['max_loss_streak']} pérdidas seguidas", "evidence": f"peores: {'; '.join(d['worst'])}", "cost_usd": round(s["gross_loss"])})
            tomorrow.append("Tras 2 pérdidas seguidas, parar 30 minutos o cerrar el día.")
        if s["avg_loss"] and s["avg_win"] and abs(s["avg_loss"]) > 1.5 * s["avg_win"]:
            patterns.append({"title": "Pérdida media mucho mayor que la ganancia media", "evidence": f"media ganadora {s['avg_win']:+.0f} $, media perdedora {s['avg_loss']:+.0f} $", "cost_usd": None})
            tomorrow.append("Objetivo de al menos 1,5 veces el stop (botón 1.5R o 2R) y dejar correr con trailing.")
        hours = sorted(d["by_hour"], key=lambda b: b["net"])
        if hours and hours[0]["net"] < 0 and hours[0]["n"] >= 2:
            patterns.append({"title": f"La peor hora fue las {hours[0]['key']}h", "evidence": f"{hours[0]['n']} operaciones, {hours[0]['net']:+.0f} $", "cost_usd": round(hours[0]["net"])})
        if d["day_closed"]:
            patterns.append({"title": "Saltó el día cerrado", "evidence": "la pérdida conjunta alcanzó el límite global", "cost_usd": None})
            tomorrow.append("Mitad de tamaño hasta recuperar la mitad de lo perdido.")
        if d["rejections"]:
            patterns.append({"title": f"{len(d['rejections'])} órdenes rechazadas", "evidence": d["rejections"][0], "cost_usd": None})
        for extra in ("Pon el stop a breakeven en 1R y deja correr el resto con trailing.", "Revisa el calendario de noticias antes de la apertura.", "Mantén el tamaño y las reglas de hoy."):
            if len(tomorrow) >= 3:
                break
            if extra not in tomorrow:
                tomorrow.append(extra)
        score = max(0, min(10, 8 - len(d["quick_stops"]) - (2 if s["max_loss_streak"] >= 3 else 0) - (2 if d["day_closed"] else 0) - (1 if d["near_news"] else 0)))
        mood = "bien" if s["net"] > 0 and score >= 6 else "mal" if s["net"] < 0 and score <= 4 else "regular"
        summary = (f"{s['n']} operaciones, {s['wins']} ganadoras ({(s['win_rate'] or 0) * 100:.0f} %), neto {s['net']:+,.0f} $ (factor de beneficio {s['pf'] or '—'}). "
                   f"Mejor {d['best'][0] if d['best'] else '—'}; peor {d['worst'][0] if d['worst'] else '—'}. "
                   + (f"{len(d['quick_stops'])} stops en menos de un minuto. " if d["quick_stops"] else "") + (f"{len(d['near_news'])} entradas pegadas a una noticia." if d["near_news"] else ""))
        return {"summary": summary, "patterns": patterns[:5], "tomorrow": tomorrow[:3], "score": score, "mood": mood}

    async def review(self, day: str | None = None, force: bool = False) -> dict:
        day = day or self.now().strftime("%Y-%m-%d")
        key = f"ai.review.{day}"
        if not force and self.store is not None:
            raw = self.store.get_kv(key)
            if raw:
                try:
                    cached = json.loads(raw)
                    if cached.get("source") == "ia" or not self.ai.enabled:
                        return cached
                except ValueError:
                    pass
        d = self._day_data(day)
        out = {"day": day, "stats": d["stats"], "by_hour": d["by_hour"], "by_account": d["by_account"], "news": d["news"], "near_news": d["near_news"], "quick_stops": d["quick_stops"],
               "audit_counts": d["audit_counts"], "generated_at": self.now().astimezone().isoformat(timespec="seconds")}
        if self.ai.enabled and d["stats"]["n"] > 0:
            try:
                prompt = json.dumps({k: v for k, v in d.items() if k != "trades"} | {"trades": [ts.brief(t) for t in d["trades"][:60]]}, ensure_ascii=False, default=str)
                r = await self.ai.ask(REVIEW_SYSTEM, "Datos de la sesión:\n" + prompt + "\n¿Qué me costó dinero y qué cambio mañana?")
                out |= {"source": "ia", "model": r.pop("model", self.ai.model), "review": r}
            except Exception as exc:
                logger.warning(f"Revisión IA del {day}: {exc}")
                out |= {"source": "regla", "model": "", "review": self._heuristic_review(d), "ai_error": str(exc)[:160]}
        else:
            out |= {"source": "regla", "model": "", "review": self._heuristic_review(d)}
        if self.store is not None:
            self.store.set_kv(key, json.dumps(out, ensure_ascii=False, default=str))
        return out

    # ---- semáforo antes de entrar ----
    async def check_entry(self, order: dict, use_ai: bool = False) -> dict:
        acc = str(order.get("account") or "")
        symbol = str(order.get("symbol") or "")
        qty = int(order.get("quantity") or 0)
        reasons: list[dict] = []
        add = lambda level, text: reasons.append({"level": level, "text": text})  # noqa: E731
        st = self.risk.state()
        if st.kill_switch:
            add("red", "kill switch activo" + (": día cerrado" if st.day_closed else ""))
        if st.day_loss_limit > 0 and st.day_total <= -0.7 * st.day_loss_limit:
            add("red" if st.day_total <= -0.9 * st.day_loss_limit else "amber", f"pérdida conjunta del día {st.day_total:,.0f} $ frente al límite −{st.day_loss_limit:,.0f} $")
        snap = self.accounts.find(acc)
        if snap is not None:
            lim = self.risk.limits.get(snap.account_id)
            dd = snap.drawdown
            if dd.pct is not None:
                if dd.pct >= 85:
                    add("red", f"drawdown al {dd.pct:.0f} %: quedan {dd.room or 0:,.0f} $")
                elif dd.pct >= 60:
                    add("amber", f"drawdown al {dd.pct:.0f} %: quedan {dd.room or 0:,.0f} $")
            if lim is not None:
                if lim.trading_halted:
                    add("red", f"cuenta pausada por riesgo ({lim.halted_reason or 'manual'})")
                if lim.max_daily_loss > 0 and snap.net_pnl <= -0.7 * lim.max_daily_loss:
                    add("amber" if snap.net_pnl > -lim.max_daily_loss else "red", f"P&L del día {snap.net_pnl:,.0f} $ frente a la pérdida diaria −{lim.max_daily_loss:,.0f} $")
                if lim.max_position_size and qty > lim.max_position_size:
                    add("red", f"{qty} contratos supera el máximo {lim.max_position_size} de la cuenta")
                if lim.max_position_size and qty >= 0.8 * lim.max_position_size:
                    add("amber", f"{qty} contratos es casi el máximo ({lim.max_position_size})")
            elif not (snap.firm or ""):
                add("amber", "cuenta sin límites de riesgo ni perfil")
            pos = next((p for p in snap.open_positions if p.symbol.lower() == symbol.lower() and p.quantity), None)
            if pos and ((pos.quantity > 0) == (str(order.get("action", "")).upper() == "BUY")):
                add("amber", f"ya hay posición {'larga' if pos.quantity > 0 else 'corta'} de {abs(pos.quantity)}: esto la aumenta")
        # racha de hoy
        day = self.now().strftime("%Y-%m-%d")
        trades = self.store.get_trades(day, day, acc) if self.store and acc else []
        streak = 0
        for t in sorted(trades, key=lambda t: t.get("closed_at") or "", reverse=True):
            if ts.net_of(t) < 0:
                streak += 1
            else:
                break
        if streak >= 3:
            add("red", f"{streak} pérdidas seguidas hoy en esta cuenta")
        elif streak == 2:
            add("amber", "2 pérdidas seguidas hoy en esta cuenta")
        # noticias
        try:
            al = self.news.alerts() if self.news else {"upcoming": [], "results": []}
            for u in al.get("upcoming", []):
                if u["minutes"] <= 10:
                    add("red" if u["event"]["impact"] == "high" else "amber", f"{u['event']['title']} en {u['minutes']} min (impacto {u['event']['impact']})")
            for r in al.get("results", []):
                if -10 <= r["minutes"] < 0 and r["event"]["impact"] == "high":
                    add("amber", f"{r['event']['title']} publicado hace {-r['minutes']} min: el mercado aún lo digiere")
        except Exception:
            pass
        # volatilidad y datos de precio
        if self.market is not None and symbol:
            age = self.market.age_s(symbol)
            if age is None:
                add("amber", "sin precio en vivo de este instrumento")
            elif age > 30:
                add("amber", f"último tick hace {age:.0f} s")
            atr_now, atr_long = self.market.atr_ticks(symbol, 5), self.market.atr_ticks(symbol, 60)
            if atr_now and atr_long and atr_now > 1.8 * atr_long:
                add("amber", f"volatilidad disparada: ATR 5 min {atr_now:.0f} ticks frente a {atr_long:.0f} de media")
            sl = int(order.get("sl_ticks") or 0)
            if sl and atr_now and sl < 0.5 * atr_now:
                add("amber", f"stop de {sl} ticks por debajo de la mitad del ATR ({atr_now:.0f} ticks): probable ruido")
            if not sl and not str(order.get("note") or "").startswith("cierre"):
                add("amber", "entrada sin stop automático")
        verdict = "red" if any(r["level"] == "red" for r in reasons) else "amber" if any(r["level"] == "amber" for r in reasons) else "green"
        out = {"verdict": verdict, "reasons": reasons, "checked_at": self.now().astimezone().isoformat(timespec="seconds"), "ai": None}
        if use_ai and self.ai.enabled:
            try:
                r = await self.ai.ask(CHECK_SYSTEM, json.dumps({"order": order, "signals": reasons, "verdict_rules": verdict}, ensure_ascii=False, default=str), max_tokens=300)
                out["ai"] = {"verdict": r.get("verdict"), "reason": r.get("reason"), "model": r.get("model")}
            except Exception as exc:
                out["ai"] = {"error": str(exc)[:160]}
        return out

    # ---- chat con tus datos ----
    def _context(self, days: int = 90) -> dict:
        end = self.now()
        start = end - timedelta(days=days)
        trades = self.store.get_trades(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")) if self.store else []
        return {"period_days": days, "n_trades": len(trades), "stats": ts.trade_stats(trades), "by_weekday": ts.by_weekday(trades), "by_hour": ts.by_hour(trades),
                "by_symbol": ts.by_symbol(trades), "by_account": ts.by_account(trades)[:12], "by_side": ts.by_side(trades),
                "best": [ts.brief(t) for t in ts.best(trades, 5)], "worst": [ts.brief(t) for t in ts.worst(trades, 5)],
                "last_trades": [f"{t['day']} {ts.brief(t)}" for t in sorted(trades, key=lambda t: t.get("closed_at") or "")[-25:]]}

    async def ask(self, question: str, days: int = 90) -> dict:
        q = " ".join(str(question or "").split())[:400]
        if not q:
            raise ValueError("escribe una pregunta")
        ctx = self._context(days)
        if not self.ai.enabled:
            raise RuntimeError("el chat con tus datos necesita ANTHROPIC_API_KEY en engine/.env")
        t0 = time.monotonic()
        r = await self.ai.ask(ASK_SYSTEM, "Datos (últimos %d días):\n%s\n\nPregunta: %s" % (days, json.dumps(ctx, ensure_ascii=False, default=str), q))
        return {"question": q, "answer": r.get("answer") or r.get("summary") or "", "numbers": r.get("numbers") or [], "model": r.get("model"), "ms": round((time.monotonic() - t0) * 1000),
                "context": {"n_trades": ctx["n_trades"], "period_days": days}}
