"""Reglas de prop firm: cuánto falta para el payout y aviso antes de romper una regla.

Por cuenta (firma + plan del perfil, saldo inicial del límite, P&L diario reportado por NinjaTrader, operaciones y retiros
anotados) calcula:
- **ganancia acumulada** sobre el saldo inicial y **colchón de seguridad** (Apex: saldo ≥ inicial + drawdown + 100 $);
- **días operados** y **días ganadores** (≥ `min_day_profit`) frente al mínimo de la firma;
- **consistencia**: el mejor día no puede pasar del X % del total ganado; cuánto puede ganar HOY sin romperla;
- **retiros**: pedidos y pagados (movimientos de caja), mínimo y máximo por retiro.

Cada regla sale con estado ok / pendiente / bloqueada y el "falta …" concreto; la cuenta es elegible para payout cuando
todas están ok. Las cifras por firma están en RULES (orientativas, cambian: se pueden corregir por cuenta en la consola y
se guardan en kv `proprules.<cuenta>`). Cada 5 min, si el P&L de hoy se acerca al máximo que permite la consistencia
(≥ 80 %), se audita PROP_RULE_WARN (una vez al día por cuenta) para avisar antes de romperla.
"""
import asyncio
import json
from datetime import datetime, timedelta

from loguru import logger

# consistency: mejor día / total ganado máximo; min_days: días operados antes del primer payout; min_day_profit: lo que
# cuenta como "día ganador" (0 = cualquier día con operaciones); safety_net: ganancia mínima extra sobre el drawdown
# (Apex: dd + 100 $); payout_min/max: límites por retiro ($; max 0 = sin tope); payout_max_pct: % del beneficio retirable
RULES: dict[str, dict] = {
    # Apex Legacy PA (orientativo, confirmado con la ayuda de Apex el 2/11/2026): 8 días operados, 5 días con ≥ 50 $, consistencia
    # 30 % hasta el 5.º retiro, colchón = drawdown + 100 $ (50K: 52.600), retiro mín. 500 $, máx. 2.000 $ en los 5 primeros.
    "apex": {"consistency": 0.30, "min_days": 8, "min_day_profit": 50, "safety_net": True, "safety_extra": 100, "payout_min": 500, "payout_max": 2000, "payout_max_pct": 100, "winning_days": 5,
             "payout_max_until": 5, "consistency_until": 5},
    "topstep": {"consistency": 0.50, "min_days": 0, "min_day_profit": 200, "safety_net": False, "safety_extra": 0, "payout_min": 125, "payout_max": 5000, "payout_max_pct": 50, "winning_days": 5, "payout_max_until": 0, "consistency_until": 0},
    "mff": {"consistency": 0.40, "min_days": 1, "min_day_profit": 0, "safety_net": False, "safety_extra": 0, "payout_min": 250, "payout_max": 0, "payout_max_pct": 100, "winning_days": 0, "payout_max_until": 0, "consistency_until": 0},
    "tpt": {"consistency": 0.50, "min_days": 5, "min_day_profit": 0, "safety_net": False, "safety_extra": 0, "payout_min": 500, "payout_max": 0, "payout_max_pct": 100, "winning_days": 0, "payout_max_until": 0, "consistency_until": 0},
}
# Reglas de la EVALUACIÓN por firma (orientativas, editables por cuenta): días mínimos operados, consistencia (mejor día ≤ X %
# del total ganado; 0 = sin regla), qué cuenta como día operado (min_day_profit) y contratos al pasar a "mantenimiento"
# (objetivo conseguido pero faltan días: se baja el tamaño para no perderlo).
EVAL_RULES: dict[str, dict] = {
    "apex": {"consistency": 0.0, "min_days": 7, "min_day_profit": 0, "maint_contracts": 1},
    "topstep": {"consistency": 0.50, "min_days": 2, "min_day_profit": 0, "maint_contracts": 1},
    "mff": {"consistency": 0.40, "min_days": 1, "min_day_profit": 0, "maint_contracts": 1},
    "tpt": {"consistency": 0.50, "min_days": 5, "min_day_profit": 0, "maint_contracts": 1},
}
DEFAULT_EVAL = {"consistency": 0.0, "min_days": 0, "min_day_profit": 0, "maint_contracts": 1}
DEFAULT_RULE = {"consistency": 0.40, "min_days": 5, "min_day_profit": 0, "safety_net": False, "safety_extra": 0, "payout_min": 500, "payout_max": 0, "payout_max_pct": 100, "winning_days": 0, "payout_max_until": 0, "consistency_until": 0}
EVAL_PLANS = {"eval", "combine", "starter", "expert", "test", "static"}


class PropRulesService:
    def __init__(self, store, accounts, risk, audit, interval_s: int = 300) -> None:
        self.store, self.accounts, self.risk, self.audit, self.interval_s = store, accounts, risk, audit, interval_s
        self.now = datetime.now
        self._task: asyncio.Task | None = None
        self._warned: dict[str, str] = {}
        self._waited: dict[str, str] = {}          # cuenta -> día en que ya se avisó "objetivo conseguido, faltan días"
        if risk is not None:
            risk.goal_gate = self.goal_gate

    # ---- reglas por cuenta (firma + ajustes guardados) ----
    def rule_for(self, account_id: str, firm: str) -> dict:
        base = dict(RULES.get((firm or "").lower(), DEFAULT_RULE))
        raw = self.store.get_kv(f"proprules.{account_id}") if self.store else None
        if raw:
            try:
                base.update({k: v for k, v in json.loads(raw).items() if k in base})
            except ValueError:
                pass
        return base

    def set_rule(self, account_id: str, changes: dict) -> dict:
        snap = self.accounts.find(account_id)
        cur = self.rule_for(account_id, snap.firm if snap else "")
        cur.update({k: v for k, v in changes.items() if k in cur and v is not None})
        if self.store:
            self.store.set_kv(f"proprules.{account_id}", json.dumps(cur))
        return cur

    # ---- modo evaluación: reglas de la prueba por cuenta ----
    def eval_rule_for(self, account_id: str, firm: str) -> dict:
        base = {"auto": False, **EVAL_RULES.get((firm or "").lower(), DEFAULT_EVAL)}
        raw = self.store.get_kv(f"evalrules.{account_id}") if self.store else None
        if raw:
            try:
                base.update({k: v for k, v in json.loads(raw).items() if k in base})
            except ValueError:
                pass
        return base

    def set_eval_rule(self, account_id: str, changes: dict) -> dict:
        snap = self.accounts.find(account_id)
        cur = self.eval_rule_for(account_id, snap.firm if snap else "")
        cur.update({k: v for k, v in changes.items() if k in cur and v is not None})
        if self.store:
            self.store.set_kv(f"evalrules.{account_id}", json.dumps(cur))
        return cur

    def goal_gate(self, account_id: str) -> bool:
        """Lo consulta el riesgo antes de cerrar por objetivo: False = la evaluación sigue (faltan días o consistencia)."""
        try:
            r = self.evaluate(account_id)
        except Exception:
            return True
        if not r or not r["is_eval"] or not r["eval_rule"]["auto"]:
            return True
        # con el gestor activo, el cierre por objetivo lo hace guard() (EVAL_PASSED, con días y consistencia comprobados)
        return r["status"] not in ("target_wait", "passed")

    async def guard(self) -> None:
        """Tras cada sincronización: el gestor automático de las evaluaciones con `auto` activado."""
        if self.risk is None or self.accounts is None:
            return
        today = self.now().strftime("%Y-%m-%d")
        for a in list(self.accounts.accounts.values()):
            if not a.enabled:
                continue
            lim = self.risk.limits.get(a.account_id)
            # la parada por consistencia caduca sola al día siguiente
            if lim is not None and lim.trading_halted and lim.halted_reason == "consistency" and lim.halted_at and lim.halted_at.strftime("%Y-%m-%d") < today:
                self.risk.upsert_limit(lim.model_copy(update={"trading_halted": False, "halted_reason": "", "halted_at": None}))
                self.audit.log("EVAL_RESUMED", f"{a.account_id}: nuevo día, la parada por consistencia de ayer se levanta", target=a.account_id)
                lim = self.risk.limits.get(a.account_id)
            r = self.evaluate(a.account_id)
            if not r or not r["is_eval"] or not r["eval_rule"]["auto"] or lim is None or lim.trading_halted:
                continue
            # 1) tope de consistencia de hoy
            if r["max_today"] is not None and r["max_today"] > 0 and r["today"] >= r["max_today"]:
                await self.risk.halt_account(a.account_id, "consistency", "EVAL_CONSISTENCY_STOP",
                                             f"{a.account_id}: hoy lleva {r['today']:+,.0f} $ y la consistencia del {r['eval_rule']['consistency'] * 100:.0f} % permite como máximo {r['max_today']:,.0f} $ hoy. Cuenta cerrada y parada hasta mañana para no romper la regla.",
                                             "tope de consistencia de hoy", {"today": r["today"], "max_today": r["max_today"]})
                continue
            # 2) objetivo conseguido pero faltan días / consistencia: modo mantenimiento (tamaño mínimo), sin cerrar la cuenta
            if r["status"] == "target_wait":
                mc = int(r["eval_rule"]["maint_contracts"] or 0)
                if mc > 0 and (lim.max_position_size == 0 or lim.max_position_size > mc):
                    self.risk.upsert_limit(lim.model_copy(update={"max_position_size": mc}))
                if self._waited.get(a.account_id) != today:
                    self._waited[a.account_id] = today
                    pend = "; ".join(x["label"] + (f" (faltan {x['missing']:g})" if x.get("missing") else "") for x in r["requirements"] if x["status"] != "ok")
                    self.audit.log("EVAL_TARGET_WAIT", f"{a.account_id}: objetivo de la evaluación conseguido ({r['profit']:+,.0f} $) pero aún falta: {pend}. "
                                   + (f"Tamaño limitado a {mc} contrato{'s' if mc != 1 else ''} para no perderlo; " if mc > 0 else "")
                                   + "sigue operando con cabeza hasta cumplir lo que falta.", target=a.account_id, details={"profit": r["profit"], "pending": pend})
                continue
            # 3) todo cumplido: evaluación superada
            if r["status"] == "passed":
                await self.risk.halt_account(a.account_id, "profit_goal", "EVAL_PASSED",
                                             f"🏆 {a.account_id}: EVALUACIÓN SUPERADA. Ganancia {r['profit']:+,.0f} $, {r['traded_days']} días operados, consistencia OK. Cuenta cerrada y pausada: no la toques hasta tener la cuenta financiada.",
                                             "evaluación superada", {"profit": r["profit"], "days": r["traded_days"]})

    # ---- evaluación ----
    def evaluate(self, account_id: str) -> dict | None:
        # las reglas de payout necesitan todo el historial de la cuenta, haya o no una etapa activa
        from tradepilot.core.scope import stage_bypass
        with stage_bypass():
            return self._evaluate(account_id)

    def _evaluate(self, account_id: str) -> dict | None:
        snap = self.accounts.find(account_id)
        if snap is None:
            return None
        lim = self.risk.limits.get(snap.account_id)
        rule = self.rule_for(snap.account_id, snap.firm)
        start = float(lim.start_balance) if lim and lim.start_balance > 0 else float(snap.plan_size or 0)
        today = self.now().strftime("%Y-%m-%d")
        frm = (self.now() - timedelta(days=120)).strftime("%Y-%m-%d")
        rows = self.store.get_daily_pnl(frm, today, snap.account_id) if self.store else []
        days = {r["day"]: float(r["pnl"] or 0) for r in rows if r.get("pnl") is not None}
        trades_days = {t["day"] for t in (self.store.get_trades(frm, today, snap.account_id) if self.store else [])}
        for d in trades_days:
            days.setdefault(d, 0.0)
        if today not in days and snap.net_pnl:
            days[today] = snap.net_pnl
        elif today in days and snap.net_pnl:
            days[today] = snap.net_pnl
        traded = sorted(d for d, v in days.items() if v != 0 or d in trades_days)
        winning = [d for d in traded if days[d] >= max(rule["min_day_profit"], 0.01)]
        total = round(sum(v for v in days.values() if v is not None), 2)
        profit = round(snap.balance + snap.unrealized_pnl - start, 2) if start > 0 else total
        best_day = max(days.values()) if days else 0.0
        # la consistencia se mide sobre lo ganado en los días (incluye lo ya retirado), no sobre el saldo
        best_share = (best_day / total) if total > 0 else None
        before_today = total - days.get(today, 0.0)
        thr = float(rule["consistency"])
        # máximo que puede ganar hoy sin que hoy pase a ser > thr del total: hoy ≤ thr·(antes + hoy) → hoy ≤ thr·antes/(1−thr)
        max_today = (thr * before_today / (1 - thr)) if (thr < 1 and before_today > 0) else None
        payouts = [c for c in (self.store.list_cash_events(snap.account_id) if self.store else []) if c["kind"] == "withdrawal" and c["status"] != "rejected"]
        paid = round(sum(float(c["paid"] or 0) for c in payouts if c.get("paid") is not None), 2)
        requested = round(sum(float(c["amount"] or 0) for c in payouts), 2)
        ev = self.eval_rule_for(snap.account_id, snap.firm)
        # modo evaluación: por el plan del perfil o porque el gestor automático se activó a mano en esa cuenta
        is_eval = (snap.plan or "").lower() in EVAL_PLANS or bool(ev["auto"])
        reqs: list[dict] = []
        add = lambda key, label, ok, detail, missing=None: reqs.append({"key": key, "label": label, "status": "ok" if ok else "pending", "detail": detail, "missing": missing})  # noqa: E731
        status = None
        if is_eval:
            goal = float(lim.profit_goal) if lim and lim.profit_goal > 0 else 0.0
            if goal > 0:
                add("goal", "Objetivo de la evaluación", profit >= goal, f"{profit:+,.0f} $ de {goal:,.0f} $", max(0.0, goal - profit))
            ethr = float(ev["consistency"] or 0)
            ewin = [d for d in traded if days[d] >= max(float(ev["min_day_profit"] or 0), 0.01)] if float(ev["min_day_profit"] or 0) > 0 else traded
            if int(ev["min_days"] or 0) > 0:
                add("days", f"Días operados (mín. {ev['min_days']})" + (f" con ≥ {float(ev['min_day_profit']):,.0f} $" if float(ev["min_day_profit"] or 0) > 0 else ""),
                    len(ewin) >= int(ev["min_days"]), f"{len(ewin)} de {ev['min_days']}", max(0, int(ev["min_days"]) - len(ewin)))
            if 0 < ethr < 1:
                ok = best_share is None or best_share <= ethr
                add("consistency", f"Consistencia (mejor día ≤ {ethr * 100:.0f} % del total)", ok,
                    f"mejor día {best_day:+,.0f} $ = {best_share * 100:.0f} % de {total:+,.0f} $ ganados" if best_share is not None else "sin ganancia acumulada aún",
                    max(0.0, best_day / ethr - total) if (best_share is not None and not ok) else 0.0)
                max_today = (ethr * before_today / (1 - ethr)) if before_today > 0 else None
            else:
                max_today = None      # la evaluación no tiene regla de consistencia: el tope de la financiada no aplica
            goal_ok = goal > 0 and profit >= goal
            others_ok = all(x["status"] == "ok" for x in reqs if x["key"] != "goal")
            status = "passed" if (goal_ok and others_ok) else "target_wait" if goal_ok else "in_progress"
            if lim is not None and lim.trading_halted and lim.halted_reason == "consistency":
                status = "consistency_stop"
        else:
            dd = float(lim.max_trailing_drawdown) if lim and lim.max_trailing_drawdown > 0 else 0.0
            if rule["safety_net"] and dd > 0:
                need = dd + float(rule["safety_extra"])
                add("safety_net", "Colchón de seguridad", profit >= need, f"ganancia {profit:+,.0f} $ de {need:,.0f} $ (drawdown {dd:,.0f} $ + {rule['safety_extra']:,.0f} $)", max(0.0, need - profit))
            if rule["min_days"] > 0:
                add("days", f"Días operados (mín. {rule['min_days']})", len(traded) >= rule["min_days"], f"{len(traded)} de {rule['min_days']}", max(0, rule["min_days"] - len(traded)))
            if rule["winning_days"] > 0:
                add("winning_days", f"Días ganadores de ≥ {rule['min_day_profit']:,.0f} $ (mín. {rule['winning_days']})", len(winning) >= rule["winning_days"], f"{len(winning)} de {rule['winning_days']}", max(0, rule["winning_days"] - len(winning)))
            cons_until = int(rule.get("consistency_until") or 0)
            if cons_until > 0 and len(payouts) >= cons_until:
                thr = 1.0       # la consistencia deja de aplicar a partir de ese retiro (Apex Legacy: tras el 5.º)
                max_today = None
            if thr < 1:
                ok = best_share is not None and best_share <= thr
                need_total = (best_day / thr) if thr > 0 else 0
                add("consistency", f"Consistencia (mejor día ≤ {thr * 100:.0f} % del total)", ok or total <= 0,
                    f"mejor día {best_day:+,.0f} $ = {best_share * 100:.0f} % de {total:+,.0f} $ ganados" if best_share is not None else "sin ganancia acumulada aún",
                    max(0.0, need_total - total) if (best_share is not None and not ok) else 0.0)
            if rule["payout_min"] > 0:
                add("payout_min", f"Beneficio retirable (mín. {rule['payout_min']:,.0f} $)", profit >= rule["payout_min"], f"{profit:+,.0f} $", max(0.0, rule["payout_min"] - profit))
        eligible = (status == "passed") if is_eval else (bool(reqs) and all(r["status"] == "ok" for r in reqs))
        available = 0.0
        if not is_eval and profit > 0:
            available = profit * rule["payout_max_pct"] / 100
            cap_until = int(rule.get("payout_max_until") or 0)
            if rule["payout_max"] > 0 and (cap_until == 0 or len(payouts) < cap_until):
                available = min(available, rule["payout_max"])
        return {"account_id": snap.account_id, "alias": snap.alias, "firm": snap.firm, "plan": snap.plan, "is_eval": is_eval, "start_balance": start, "balance": snap.balance, "profit": profit,
                "total_days": total, "today": days.get(today, 0.0), "traded_days": len(traded), "winning_days": len(winning), "best_day": best_day, "best_day_share": round(best_share * 100, 1) if best_share is not None else None,
                "max_today": round(max_today, 2) if max_today is not None else None, "today_room": round(max_today - days.get(today, 0.0), 2) if max_today is not None else None,
                "payouts_requested": requested, "payouts_paid": paid, "n_payouts": len(payouts), "available": round(available, 2), "eligible": eligible, "requirements": reqs, "rule": rule,
                "eval_rule": ev, "status": status, "goal": float(lim.profit_goal) if lim and lim.profit_goal > 0 else 0.0}

    def evaluate_all(self) -> list[dict]:
        out = []
        for a in self.accounts.accounts.values():
            if a.enabled and (a.firm or a.plan):
                r = self.evaluate(a.account_id)
                if r:
                    out.append(r)
        return sorted(out, key=lambda r: (r["is_eval"], not r["eligible"], r["account_id"]))

    # ---- aviso antes de romper la consistencia ----
    async def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                self.check()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Reglas de prop firm: {exc}")
            await asyncio.sleep(self.interval_s)

    def check(self) -> list[str]:
        warned = []
        today = self.now().strftime("%Y-%m-%d")
        for r in self.evaluate_all():
            if r["max_today"] is None or r["max_today"] <= 0:
                continue
            if r["today"] >= 0.8 * r["max_today"] and self._warned.get(r["account_id"]) != today:
                self._warned[r["account_id"]] = today
                over = r["today"] > r["max_today"]
                thr_pct = (r["eval_rule"]["consistency"] if r["is_eval"] else r["rule"]["consistency"]) * 100
                self.audit.log("PROP_RULE_WARN", f"{r['account_id']}: hoy lleva {r['today']:+,.0f} $ y la consistencia del {thr_pct:.0f} % permite como máximo {r['max_today']:,.0f} $ hoy"
                               + (" — YA SUPERADO: el resto de días tendrá que crecer antes del payout" if over else f" — quedan {r['today_room']:,.0f} $; baja tamaño o para por hoy"),
                               target=r["account_id"], details={"today": r["today"], "max_today": r["max_today"]})
                warned.append(r["account_id"])
        return warned
