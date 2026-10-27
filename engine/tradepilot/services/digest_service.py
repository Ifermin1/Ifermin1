"""Resúmenes automáticos: el **briefing de la mañana** y el **cierre de sesión**, a la hora que elijas, al teléfono.

- Briefing (MORNING_BRIEF_TIME, hora local del PC): noticias vigiladas del día con hora e impacto, estado de las cuentas
  (drawdown restante, pérdida diaria, kill switch) y lo que la IA recomienda vigilar (ventanas sin operar, tamaño).
  Sin clave, el mismo resumen por reglas.
- Cierre (SESSION_REVIEW_TIME): la revisión del coach del día (qué costó dinero, tres reglas para mañana, nota) generada
  sola y enviada por Telegram/push (SESSION_REVIEW).

Cada uno se manda una vez al día (kv digest.<kind>.<día>), solo en días con mercado abierto (lunes a viernes) salvo que se
pida a mano. Los textos quedan guardados para verlos en la consola (Inicio → Briefing y cierre).
"""
import asyncio
import json
from datetime import datetime

from loguru import logger

BRIEF_SYSTEM = (
    "Eres el coach de un trader intradía de futuros (NQ/ES/CL) con cuentas de prop firm. Te paso las noticias económicas de hoy con hora local, "
    "el estado de sus cuentas (drawdown restante, pérdida diaria, racha de ayer) y sus reglas recientes. Escribe un briefing de la mañana en "
    "español, concreto, máximo 8 líneas: qué datos mueven el mercado hoy y a qué hora, ventanas en las que NO entrar, con qué tamaño "
    "empezar y una regla del día. Devuelve ÚNICAMENTE un JSON: {\"brief\": \"texto con saltos de línea\", \"no_trade_windows\": [\"HH:MM-HH:MM motivo\"], \"size_hint\": \"...\"}"
)


class DigestService:
    def __init__(self, store, audit, news, coach, accounts, risk, ai, brief_time: str = "08:00", review_time: str = "17:05", enabled: bool = True) -> None:
        self.store, self.audit, self.news, self.coach, self.accounts, self.risk, self.ai = store, audit, news, coach, accounts, risk, ai
        self.now = datetime.now
        cfg = json.loads(store.get_kv("digest.config") or "{}") if store else {}
        self.config = {"enabled": bool(cfg.get("enabled", enabled)), "brief_time": str(cfg.get("brief_time", brief_time)), "review_time": str(cfg.get("review_time", review_time)),
                       "weekdays_only": bool(cfg.get("weekdays_only", True))}
        self._task: asyncio.Task | None = None

    # ---- configuración ----
    def set_config(self, **changes) -> dict:
        for k, v in changes.items():
            if k in self.config and v is not None:
                self.config[k] = v
        if self.store:
            self.store.set_kv("digest.config", json.dumps(self.config))
        return self.config

    def _sent(self, kind: str, day: str) -> bool:
        return bool(self.store and self.store.get_kv(f"digest.{kind}.{day}"))

    def _mark(self, kind: str, day: str, text: str) -> None:
        if self.store:
            self.store.set_kv(f"digest.{kind}.{day}", json.dumps({"at": self.now().astimezone().isoformat(timespec="seconds"), "text": text}))
            self.store.set_kv(f"digest.last.{kind}", day)

    def last(self, kind: str) -> dict | None:
        if not self.store:
            return None
        day = self.store.get_kv(f"digest.last.{kind}")
        raw = self.store.get_kv(f"digest.{kind}.{day}") if day else None
        return {**json.loads(raw), "day": day} if raw else None

    # ---- ciclo ----
    async def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Resúmenes: {exc}")

    async def tick(self) -> None:
        if not self.config["enabled"]:
            return
        now = self.now()
        if self.config["weekdays_only"] and now.weekday() >= 5:
            return
        day, hm = now.strftime("%Y-%m-%d"), now.strftime("%H:%M")
        if hm >= self.config["brief_time"] and not self._sent("brief", day) and (now.strftime("%H:%M") <= self._plus(self.config["brief_time"], 120)):
            await self.morning_brief("automático")
        if hm >= self.config["review_time"] and not self._sent("review", day) and (now.strftime("%H:%M") <= self._plus(self.config["review_time"], 180)):
            await self.session_review("automático")

    @staticmethod
    def _plus(hm: str, minutes: int) -> str:
        h, m = (int(x) for x in hm.split(":"))
        t = min(23 * 60 + 59, h * 60 + m + minutes)
        return f"{t // 60:02d}:{t % 60:02d}"

    # ---- briefing ----
    def _today_events(self) -> list[dict]:
        day = self.now().strftime("%Y-%m-%d")
        out = []
        for e in (self.news._events if self.news is not None else []):
            try:
                if not self.news._watched(e):
                    continue
                t = datetime.fromisoformat(e["time"]).astimezone()
                if t.strftime("%Y-%m-%d") == day:
                    out.append({"time": t.strftime("%H:%M"), "title": e["title"], "impact": e["impact"], "forecast": e.get("forecast"), "previous": e.get("previous")})
            except (KeyError, ValueError):
                continue
        return out

    def _accounts_state(self) -> list[dict]:
        out = []
        for a in self.accounts.accounts.values():
            if a.enabled:
                lim = self.risk.limits.get(a.account_id)
                out.append({"id": a.account_id, "alias": a.alias, "drawdown_room": a.drawdown.room, "drawdown_pct": a.drawdown.pct, "daily_loss_limit": lim.max_daily_loss if lim else None, "max_contracts": lim.max_position_size if lim else None})
        return out

    async def morning_brief(self, reason: str = "manual") -> dict:
        day = self.now().strftime("%Y-%m-%d")
        events = self._today_events()
        accs = self._accounts_state()
        rs = self.risk.state()
        prev = self.last("review")
        source, model = "regla", None
        hi = [e for e in events if e["impact"] == "high"]
        lines = [f"📅 Briefing {self.now().strftime('%d/%m')}"]
        if events:
            lines.append("Noticias de hoy: " + "; ".join(f"{e['time']} {e['title']} ({'ALTO' if e['impact'] == 'high' else 'medio' if e['impact'] == 'medium' else 'bajo'})" for e in events[:8]))
            if hi:
                lines.append("Sin entradas desde 5 min antes hasta 15 min después de: " + ", ".join(e["time"] for e in hi))
        else:
            lines.append("Sin noticias vigiladas hoy.")
        tight = [a for a in accs if a["drawdown_pct"] is not None and a["drawdown_pct"] >= 60]
        if tight:
            lines.append("Cuentas con poco colchón: " + ", ".join(f"{a['alias'] or a['id']} ({a['drawdown_pct']:.0f} %, quedan {a['drawdown_room']:,.0f} $)" for a in tight[:5]) + ". Mitad de tamaño en ellas.")
        if rs.kill_switch:
            lines.append("⚠️ Kill switch ACTIVO: nada se copia hasta reanudar.")
        if prev and prev.get("text"):
            rules = [l for l in prev["text"].splitlines() if l.strip().startswith(("1.", "2.", "3."))]
            if rules:
                lines.append("Reglas de ayer: " + " ".join(r.strip() for r in rules[:3]))
        text = "\n".join(lines)
        if self.ai is not None and self.ai.enabled:
            try:
                r = await self.ai.ask(BRIEF_SYSTEM, json.dumps({"date": day, "events": events, "accounts": accs, "kill_switch": rs.kill_switch, "yesterday_rules": prev["text"] if prev else None}, ensure_ascii=False, default=str), max_tokens=700)
                if r.get("brief"):
                    text = f"📅 Briefing {self.now().strftime('%d/%m')}\n" + str(r["brief"]).strip()
                    if r.get("no_trade_windows"):
                        text += "\n⛔ Sin operar: " + "; ".join(str(x) for x in r["no_trade_windows"][:4])
                    if r.get("size_hint"):
                        text += f"\n📏 Tamaño: {r['size_hint']}"
                    source, model = "ia", r.get("model")
            except Exception as exc:
                logger.warning(f"Briefing: la IA falló, se manda el de reglas: {exc}")
        self._mark("brief", day, text)
        self.audit.log("MORNING_BRIEF", text, details={"source": source, "model": model, "reason": reason, "events": len(events)})
        return {"day": day, "text": text, "source": source, "model": model}

    # ---- cierre ----
    async def session_review(self, reason: str = "manual") -> dict:
        day = self.now().strftime("%Y-%m-%d")
        r = await self.coach.review(day, force=False)
        rv = r.get("review") or {}
        st = r.get("stats") or {}
        lines = [f"🏁 Cierre {self.now().strftime('%d/%m')} · {st.get('n', 0)} op. · neto {st.get('net', 0):+,.0f} $ · disciplina {rv.get('score', '—')}/10 ({rv.get('mood', '')})", rv.get("summary", "")]
        for p in (rv.get("patterns") or [])[:3]:
            lines.append(f"• {p.get('title')}" + (f" ({p['cost_usd']:+,.0f} $)" if isinstance(p.get("cost_usd"), (int, float)) else ""))
        for i, t in enumerate((rv.get("tomorrow") or [])[:3], 1):
            lines.append(f"{i}. {t}")
        text = "\n".join(l for l in lines if l)
        self._mark("review", day, text)
        self.audit.log("SESSION_REVIEW", text, details={"source": r.get("source"), "model": r.get("model"), "reason": reason})
        return {"day": day, "text": text, "source": r.get("source"), "model": r.get("model")}

    def state(self) -> dict:
        return {"config": self.config, "brief": self.last("brief"), "review": self.last("review"), "ai": bool(self.ai is not None and self.ai.enabled)}
