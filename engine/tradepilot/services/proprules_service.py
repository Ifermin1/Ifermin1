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
    "apex": {"consistency": 0.30, "min_days": 8, "min_day_profit": 0, "safety_net": True, "safety_extra": 100, "payout_min": 500, "payout_max": 0, "payout_max_pct": 100, "winning_days": 0},
    "topstep": {"consistency": 0.50, "min_days": 0, "min_day_profit": 200, "safety_net": False, "safety_extra": 0, "payout_min": 125, "payout_max": 5000, "payout_max_pct": 50, "winning_days": 5},
    "mff": {"consistency": 0.40, "min_days": 1, "min_day_profit": 0, "safety_net": False, "safety_extra": 0, "payout_min": 250, "payout_max": 0, "payout_max_pct": 100, "winning_days": 0},
    "tpt": {"consistency": 0.50, "min_days": 5, "min_day_profit": 0, "safety_net": False, "safety_extra": 0, "payout_min": 500, "payout_max": 0, "payout_max_pct": 100, "winning_days": 0},
}
DEFAULT_RULE = {"consistency": 0.40, "min_days": 5, "min_day_profit": 0, "safety_net": False, "safety_extra": 0, "payout_min": 500, "payout_max": 0, "payout_max_pct": 100, "winning_days": 0}
EVAL_PLANS = {"eval", "combine", "starter", "expert", "test", "static"}


class PropRulesService:
    def __init__(self, store, accounts, risk, audit, interval_s: int = 300) -> None:
        self.store, self.accounts, self.risk, self.audit, self.interval_s = store, accounts, risk, audit, interval_s
        self.now = datetime.now
        self._task: asyncio.Task | None = None
        self._warned: dict[str, str] = {}

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

    # ---- evaluación ----
    def evaluate(self, account_id: str) -> dict | None:
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
        is_eval = (snap.plan or "").lower() in EVAL_PLANS
        reqs: list[dict] = []
        add = lambda key, label, ok, detail, missing=None: reqs.append({"key": key, "label": label, "status": "ok" if ok else "pending", "detail": detail, "missing": missing})  # noqa: E731
        if is_eval:
            goal = float(lim.profit_goal) if lim and lim.profit_goal > 0 else 0.0
            if goal > 0:
                add("goal", "Objetivo de la evaluación", profit >= goal, f"{profit:+,.0f} $ de {goal:,.0f} $", max(0.0, goal - profit))
        else:
            dd = float(lim.max_trailing_drawdown) if lim and lim.max_trailing_drawdown > 0 else 0.0
            if rule["safety_net"] and dd > 0:
                need = dd + float(rule["safety_extra"])
                add("safety_net", "Colchón de seguridad", profit >= need, f"ganancia {profit:+,.0f} $ de {need:,.0f} $ (drawdown {dd:,.0f} $ + {rule['safety_extra']:,.0f} $)", max(0.0, need - profit))
            if rule["min_days"] > 0:
                add("days", f"Días operados (mín. {rule['min_days']})", len(traded) >= rule["min_days"], f"{len(traded)} de {rule['min_days']}", max(0, rule["min_days"] - len(traded)))
            if rule["winning_days"] > 0:
                add("winning_days", f"Días ganadores de ≥ {rule['min_day_profit']:,.0f} $ (mín. {rule['winning_days']})", len(winning) >= rule["winning_days"], f"{len(winning)} de {rule['winning_days']}", max(0, rule["winning_days"] - len(winning)))
            if thr < 1:
                ok = best_share is not None and best_share <= thr
                need_total = (best_day / thr) if thr > 0 else 0
                add("consistency", f"Consistencia (mejor día ≤ {thr * 100:.0f} % del total)", ok or total <= 0,
                    f"mejor día {best_day:+,.0f} $ = {best_share * 100:.0f} % de {total:+,.0f} $ ganados" if best_share is not None else "sin ganancia acumulada aún",
                    max(0.0, need_total - total) if (best_share is not None and not ok) else 0.0)
            if rule["payout_min"] > 0:
                add("payout_min", f"Beneficio retirable (mín. {rule['payout_min']:,.0f} $)", profit >= rule["payout_min"], f"{profit:+,.0f} $", max(0.0, rule["payout_min"] - profit))
        eligible = (not is_eval) and bool(reqs) and all(r["status"] == "ok" for r in reqs)
        available = 0.0
        if not is_eval and profit > 0:
            available = profit * rule["payout_max_pct"] / 100
            if rule["payout_max"] > 0:
                available = min(available, rule["payout_max"])
        return {"account_id": snap.account_id, "alias": snap.alias, "firm": snap.firm, "plan": snap.plan, "is_eval": is_eval, "start_balance": start, "balance": snap.balance, "profit": profit,
                "total_days": total, "today": days.get(today, 0.0), "traded_days": len(traded), "winning_days": len(winning), "best_day": best_day, "best_day_share": round(best_share * 100, 1) if best_share is not None else None,
                "max_today": round(max_today, 2) if max_today is not None else None, "today_room": round(max_today - days.get(today, 0.0), 2) if max_today is not None else None,
                "payouts_requested": requested, "payouts_paid": paid, "n_payouts": len(payouts), "available": round(available, 2), "eligible": eligible, "requirements": reqs, "rule": rule}

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
            if r["is_eval"] or r["max_today"] is None or r["max_today"] <= 0:
                continue
            if r["today"] >= 0.8 * r["max_today"] and self._warned.get(r["account_id"]) != today:
                self._warned[r["account_id"]] = today
                over = r["today"] > r["max_today"]
                self.audit.log("PROP_RULE_WARN", f"{r['account_id']}: hoy lleva {r['today']:+,.0f} $ y la consistencia del {r['rule']['consistency'] * 100:.0f} % permite como máximo {r['max_today']:,.0f} $ hoy"
                               + (" — YA SUPERADO: el resto de días tendrá que crecer antes del payout" if over else f" — quedan {r['today_room']:,.0f} $; baja tamaño o para por hoy"),
                               target=r["account_id"], details={"today": r["today"], "max_today": r["max_today"]})
                warned.append(r["account_id"])
        return warned
