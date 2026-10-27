"""Asistente operativo por chat (consola y Telegram): lenguaje natural → acciones del engine, con confirmación.

"pon todos los stops a breakeven", "cierra la mitad de NQ en las 50K", "pausa la copia", "¿cuánto llevo hoy por cuenta?".
Con clave de IA, Claude interpreta la frase con el estado real (cuentas, etiquetas, posiciones, riesgo) y devuelve una
respuesta y, si procede, una lista de acciones. Sin clave, un intérprete por reglas entiende las frases habituales.

Nada que mueva dinero se ejecuta al interpretar: se crea un **plan** (id, acciones descritas, caduca a los 120 s) que la
consola confirma con PIN + confirmación y Telegram con un botón. Las consultas (estado, posiciones, P&L) se responden al
momento. Todo queda en la auditoría (ASSISTANT).
"""
import json
import re
import secrets
import time
from datetime import datetime

from loguru import logger

PLAN_TTL_S = 120
ASSISTANT_SYSTEM = (
    "Eres el asistente operativo de TradePilot X, un copiador de operaciones entre cuentas de prop firm en NinjaTrader. Te paso el estado real "
    "(cuentas con alias y etiquetas, posiciones, P&L del día, kill switch, límites) y una frase del trader en español. Interpreta qué quiere y responde "
    "en español, breve y concreto, citando cifras del estado cuando pregunte. Si pide hacer algo, devuelve las acciones en la lista 'actions' "
    "usando SOLO estos tipos: breakeven (stop al precio de entrada; con account+symbol o sin nada = todas las posiciones), close (cerrar una "
    "posición: account, symbol, quantity opcional para parcial), close_half (cerrar la mitad: account, symbol), flatten_account (cerrar todo en una "
    "cuenta: account), flatten_all (cerrar todo en todas), pause (kill switch), resume (quitar kill switch), set_stop (mover/crear el stop: account, "
    "symbol, price). Puedes filtrar cuentas por etiqueta con 'tag' (p. ej. las 50K) o por alias: resuelve tú los nombres contra el estado y usa los "
    "ids exactos de cuenta. Si la frase es ambigua o no se puede hacer, no devuelvas acciones y explica qué falta. Devuelve ÚNICAMENTE un JSON: "
    '{"reply": "...", "actions": [{"kind": "...", "account": "...", "symbol": "...", "quantity": n, "price": x, "tag": "..."}]}'
)
MONEY = {"breakeven", "close", "close_half", "flatten_account", "flatten_all", "pause", "resume", "set_stop"}


def _usd(v: float) -> str:
    return f"{v:+,.0f} $"


class AssistantService:
    def __init__(self, accounts, risk, trade, market, ai, audit, store=None, bridge=None) -> None:
        self.accounts, self.risk, self.trade, self.market, self.ai, self.audit, self.store, self.bridge = accounts, risk, trade, market, ai, audit, store, bridge
        self.plans: dict[str, dict] = {}
        self.now = datetime.now

    # ---- estado para la IA y para las respuestas por reglas ----
    def snapshot(self) -> dict:
        rs = self.risk.state()
        accs = []
        for a in self.accounts.accounts.values():
            if not a.enabled:
                continue
            accs.append({"id": a.account_id, "alias": a.alias, "tags": list(a.tags), "firm": a.firm, "net_pnl_today": a.net_pnl, "balance": a.balance,
                         "drawdown_room": a.drawdown.room, "positions": [{"symbol": p.symbol, "quantity": p.quantity, "avg_price": p.avg_price, "unrealized": p.unrealized_pnl} for p in a.open_positions if p.quantity]})
        return {"now": self.now().strftime("%Y-%m-%d %H:%M"), "day_total": self.risk.day_total(), "kill_switch": rs.kill_switch, "kill_switch_reason": rs.kill_switch_reason,
                "day_closed": rs.day_closed, "master": self.bridge.health.master_account if self.bridge is not None else None, "accounts": accs}

    # ---- interpretación ----
    async def handle(self, text: str, channel: str = "console") -> dict:
        q = " ".join(str(text or "").split())[:400]
        if not q:
            raise ValueError("escribe algo")
        snap = self.snapshot()
        source = "regla"
        if self.ai is not None and self.ai.enabled:
            try:
                r = await self.ai.ask(ASSISTANT_SYSTEM, json.dumps({"state": snap, "message": q}, ensure_ascii=False, default=str), max_tokens=700)
                reply, actions, source = str(r.get("reply") or ""), [a for a in (r.get("actions") or []) if isinstance(a, dict)], "ia"
            except Exception as exc:
                logger.warning(f"Asistente: la IA falló, se usan las reglas: {exc}")
                reply, actions = self._rules(q, snap)
        else:
            reply, actions = self._rules(q, snap)
        actions = self._resolve(actions, snap)
        plan = None
        if actions:
            pid = secrets.token_urlsafe(6)
            self.plans = {k: v for k, v in self.plans.items() if v["expires"] > time.time()}
            plan = {"id": pid, "actions": actions, "expires": time.time() + PLAN_TTL_S, "expires_at": datetime.fromtimestamp(time.time() + PLAN_TTL_S).astimezone().isoformat(timespec="seconds"),
                    "channel": channel, "text": q, "summary": "; ".join(a["description"] for a in actions)}
            self.plans[pid] = plan
            if not reply:
                reply = "Plan listo: " + plan["summary"] + ". Confirma para ejecutarlo."
        elif not reply:
            reply = "No he entendido qué quieres hacer. Prueba: «pon todos los stops a breakeven», «cierra la mitad de NQ en Sim101», «pausa la copia», «¿cuánto llevo hoy?»."
        self.audit.log("ASSISTANT", f"[{channel}] «{q}» → {reply[:160]}" + (f" · plan {plan['id']}: {plan['summary']}" if plan else ""), details={"source": source, "plan": plan and {k: v for k, v in plan.items() if k != 'expires'}})
        return {"reply": reply, "source": source, "plan": {k: v for k, v in plan.items() if k != "expires"} if plan else None}

    def _rules(self, q: str, snap: dict) -> tuple[str, list[dict]]:
        t = q.lower()
        accs = snap["accounts"]
        if re.search(r"\b(estado|cómo voy|como voy|cuánto llevo|cuanto llevo|p&l|pnl|resultado)\b", t):
            rows = [f"{a['alias'] or a['id']}: {_usd(a['net_pnl_today'])}" + (f" · {len(a['positions'])} pos." if a['positions'] else "") for a in accs]
            return (f"Hoy {_usd(snap['day_total'])} en {len(accs)} cuentas" + (" · kill switch ACTIVO" if snap["kill_switch"] else " · copiando") + (". " + " · ".join(rows) if rows else ""), [])
        if re.search(r"\bposicion", t):
            rows = [f"{a['id']} {'L' if p['quantity'] > 0 else 'C'}{abs(p['quantity'])} {p['symbol']} @ {p['avg_price']:g} ({_usd(p['unrealized'])})" for a in accs for p in a["positions"]]
            return (("Posiciones: " + "; ".join(rows)) if rows else "Sin posiciones abiertas.", [])
        acc = next((a["id"] for a in accs if a["id"].lower() in t or (a["alias"] and a["alias"].lower() in t)), None)
        tag = next((tg for a in accs for tg in a["tags"] if tg.lower() in t), None)
        sym = next((p["symbol"] for a in accs for p in a["positions"] if p["symbol"].split(" ")[0].lower() in t.split()), None)
        if re.search(r"breakeven|break even|\bbe\b|a la entrada", t):
            return ("", [{"kind": "breakeven", "account": acc, "symbol": sym, "tag": tag}])
        if re.search(r"\b(pausa|pausar|deten|detén|para la copia|kill)", t) and not re.search(r"reanud|quita", t):
            return ("", [{"kind": "pause"}])
        if re.search(r"\b(reanuda|reanudar|sigue copiando|quita el kill)", t):
            return ("", [{"kind": "resume"}])
        m = re.search(r"stop\D*(\d+(?:[.,]\d+)?)", t)
        if m and re.search(r"\b(mueve|pon|sube|baja|coloca)\b", t) and sym:
            return ("", [{"kind": "set_stop", "account": acc, "symbol": sym, "price": float(m.group(1).replace(",", ".")), "tag": tag}])
        if re.search(r"\b(cierra|cerrar|cierro|liquida)\b", t):
            if re.search(r"\btodo\b|\btodas\b", t) and not acc and not sym:
                return ("", [{"kind": "flatten_all"}])
            if re.search(r"\bmitad\b", t) and sym:
                return ("", [{"kind": "close_half", "account": acc, "symbol": sym, "tag": tag}])
            mq = re.search(r"\b(\d+)\b", t)
            if sym:
                return ("", [{"kind": "close", "account": acc, "symbol": sym, "quantity": int(mq.group(1)) if mq else None, "tag": tag}])
            if acc or tag:
                return ("", [{"kind": "flatten_account", "account": acc, "tag": tag}])
        return ("", [])

    def _targets(self, a: dict, snap: dict) -> list[dict]:
        """Cuentas a las que aplica una acción: id exacto, etiqueta, o todas las activas."""
        accs = snap["accounts"]
        acc, tag = (a.get("account") or "").lower(), (a.get("tag") or "").lower()
        if acc:
            return [x for x in accs if x["id"].lower() == acc or (x["alias"] or "").lower() == acc]
        if tag:
            return [x for x in accs if tag in [t.lower() for t in x["tags"]]]
        return accs

    def _resolve(self, actions: list[dict], snap: dict) -> list[dict]:
        out = []
        for a in actions:
            kind = str(a.get("kind") or "")
            if kind not in MONEY:
                continue
            if kind in ("pause", "resume", "flatten_all"):
                out.append({"kind": kind, "description": {"pause": "activar el kill switch (pausar la copia)", "resume": "quitar el kill switch", "flatten_all": "CERRAR TODO en todas las cuentas y activar el kill switch"}[kind]})
                continue
            targets = self._targets(a, snap)
            sym = str(a.get("symbol") or "").lower()
            for x in targets:
                poss = [p for p in x["positions"] if not sym or p["symbol"].lower() == sym or p["symbol"].split(" ")[0].lower() == sym.split(" ")[0]]
                if kind == "flatten_account":
                    if x["positions"]:
                        out.append({"kind": "flatten_account", "account": x["id"], "description": f"cerrar todo en {x['id']}"})
                    continue
                for p in poss:
                    q = abs(p["quantity"])
                    if kind == "breakeven":
                        out.append({"kind": "breakeven", "account": x["id"], "symbol": p["symbol"], "description": f"stop a breakeven ({p['avg_price']:g}) en {x['id']} {p['symbol']}"})
                    elif kind == "close":
                        n = int(a.get("quantity") or 0) or q
                        n = min(n, q)
                        out.append({"kind": "close", "account": x["id"], "symbol": p["symbol"], "quantity": n, "description": f"cerrar {n} de {q} {p['symbol']} en {x['id']}"})
                    elif kind == "close_half":
                        n = max(1, q // 2)
                        out.append({"kind": "close", "account": x["id"], "symbol": p["symbol"], "quantity": n, "description": f"cerrar {n} de {q} {p['symbol']} en {x['id']}"})
                    elif kind == "set_stop" and a.get("price"):
                        out.append({"kind": "set_stop", "account": x["id"], "symbol": p["symbol"], "price": float(a["price"]), "description": f"stop en {float(a['price']):g} para {x['id']} {p['symbol']}"})
        return out[:40]

    # ---- ejecución ----
    def cancel(self, plan_id: str) -> bool:
        return self.plans.pop(plan_id, None) is not None

    async def confirm(self, plan_id: str) -> dict:
        plan = self.plans.pop(plan_id, None)
        if plan is None or plan["expires"] < time.time():
            raise ValueError("ese plan caducó o no existe: vuelve a pedirlo")
        results = []
        for a in plan["actions"]:
            try:
                results.append({"action": a["description"], "ok": True, "detail": await self._execute(a)})
            except Exception as exc:
                results.append({"action": a["description"], "ok": False, "detail": str(exc)[:200]})
        ok = sum(1 for r in results if r["ok"])
        self.audit.log("ASSISTANT_RUN", f"Plan {plan_id} ejecutado ({ok}/{len(results)} bien): " + "; ".join(f"{r['action']} → {'OK' if r['ok'] else 'ERROR ' + r['detail']}" for r in results)[:600],
                       details={"plan": plan_id, "results": results})
        return {"plan": plan_id, "results": results, "ok": ok, "failed": len(results) - ok}

    async def _execute(self, a: dict) -> str:
        k = a["kind"]
        cid = "as" + secrets.token_hex(4)
        if k == "pause":
            self.risk.set_kill_switch(True, "desde el asistente")
            return "kill switch activado"
        if k == "resume":
            self.risk.set_kill_switch(False)
            return "kill switch desactivado"
        if k == "flatten_all":
            self.risk.set_kill_switch(True, "cerrar todo desde el asistente")
            done = []
            for s in list(self.accounts.accounts.values()):
                if s.enabled and (s.open_positions or s.working_orders):
                    await self.risk.flatten(s.account_id, reason="desde el asistente")
                    done.append(s.account_id)
            return "cerradas: " + (", ".join(done) if done else "ninguna tenía posición")
        if k == "flatten_account":
            await self.risk.flatten(a["account"], reason="desde el asistente")
            return "cierre enviado"
        if k == "breakeven":
            r = await self.trade.protect({"client_id": cid, "account": a["account"], "symbol": a["symbol"], "breakeven": True, "last_price": (self.market.price(a["symbol"]) if self.market is not None else 0) or 0})
            return f"stop en {r.get('sl', {}).get('price') if r.get('sl') else a.get('price', '')} ({r.get('ms')} ms)"
        if k == "close":
            r = await self.trade.close_position(a["account"], a["symbol"], cid, int(a["quantity"]))
            return f"{r.get('status')} en {r.get('ms')} ms"
        if k == "set_stop":
            r = await self.trade.protect({"client_id": cid, "account": a["account"], "symbol": a["symbol"], "sl_price": float(a["price"]), "last_price": (self.market.price(a["symbol"]) if self.market is not None else 0) or 0})
            return f"stop en {a['price']:g} ({r.get('ms')} ms)"
        raise ValueError(f"acción desconocida: {k}")
