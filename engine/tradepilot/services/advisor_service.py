"""Recomendaciones: reglas sencillas sobre el estado actual de cada cuenta que avisan antes de que duela, con la acción
que las resuelve (pasar a un preset conservador, pausar la copia, igualar, cerrar, bajar el multiplicador, poner perfil).

Se calcula bajo demanda (`GET /api/advice`) a partir de las instantáneas en memoria: nada que mantener. Cada consejo lleva
severidad (bad > warn > ok > info), código, título, detalle y acciones que la consola puede ejecutar con un clic."""
from datetime import datetime, timedelta

# regla de consistencia típica de las prop firms: el mejor día no puede ser más del X % del total ganado (para retirar)
CONSISTENCY = {"apex": 0.30, "topstep": 0.50, "mff": 0.40, "tpt": 0.50}


class AdvisorService:
    def __init__(self, accounts, risk, replication, store=None, presets_provider=None) -> None:
        self.accounts, self.risk, self.replication, self.store = accounts, risk, replication, store
        self.presets_provider = presets_provider or (lambda: [])
        self.now = datetime.now

    def _rule_of(self, acc: str):
        master = (self.accounts.bridge.health.master_account or "") if getattr(self.accounts, "bridge", None) else ""
        for r in self.replication.rules:
            if r.follower_account.lower() == acc.lower() and not r.symbol_filter and (not master or r.master_matches(master)):
                return r
        return None

    def _today_trades(self, acc: str) -> list[dict]:
        if self.store is None:
            return []
        day = self.now().strftime("%Y-%m-%d")
        try:
            return self.store.get_trades(day, day, acc)
        except Exception:
            return []

    def _consistency(self, acc: str) -> tuple[float, float] | None:
        """(mejor día, total ganado) de los últimos 60 días según daily_stats; None sin datos."""
        if self.store is None:
            return None
        try:
            frm = (self.now() - timedelta(days=60)).strftime("%Y-%m-%d")
            rows = self.store.get_daily_pnl(frm, self.now().strftime("%Y-%m-%d"), acc)
        except Exception:
            return None
        if not rows:
            return None
        pnls = [float(r["pnl"] or 0) for r in rows]
        total = sum(pnls)
        return (max(pnls) if pnls else 0.0, total)

    def advise(self) -> dict:
        advice: list[dict] = []
        presets = {p["id"]: p for p in self.presets_provider()}
        conservative = next((p for p in presets.values() if p["id"] == "conservador"), None) or (next(iter(presets.values())) if presets else None)
        master = (self.accounts.bridge.health.master_account or "") if getattr(self.accounts, "bridge", None) else ""
        for a in self.accounts.all():
            if not a.enabled:
                continue
            acc = a.account_id
            lim = self.risk.limits.get(acc)
            rule = self._rule_of(acc)
            is_master = master and acc.lower() == master.lower()
            dd = a.drawdown
            pct = dd.pct if dd.pct is not None else None
            add = lambda sev, code, title, detail, actions=None: advice.append({"account_id": acc, "severity": sev, "code": code, "title": title, "detail": detail, "actions": actions or []})  # noqa: E731
            if a.desync:
                add("bad", "DESYNC", "Desincronizada con la maestra", a.desync_detail or "La posición no coincide con la maestra × multiplicador; solo se copian salidas.",
                    [{"type": "resync", "label": "Igualar a la maestra"}, {"type": "flatten", "label": "Cerrar"}])
            if lim is None or (lim.max_trailing_drawdown <= 0 and lim.max_daily_loss <= 0 and lim.max_position_size <= 0):
                add("warn", "NO_LIMITS", "Sin límites de riesgo", "Nada frena esta cuenta antes de que lo haga el prop firm: ponle perfil (drawdown, colchón, contratos).",
                    [{"type": "profile", "label": "Aplicar perfil automático"}])
            elif pct is not None:
                room = dd.room if dd.room is not None else 0.0
                if pct >= 85:
                    acts = [{"type": "flatten", "label": "Cerrar y proteger lo que queda"}]
                    if rule and not is_master:
                        acts.insert(0, {"type": "pause", "label": "Pausar la copia"})
                    add("bad", "DD_CRITICAL", f"Drawdown al {pct:.0f} %: quedan {room:,.0f} $", "Un movimiento normal de NQ la liquida. Para o reduce ya.", acts)
                elif pct >= 60:
                    acts = []
                    if conservative and rule and not is_master:
                        acts.append({"type": "preset", "preset_id": conservative["id"], "label": f"Pasar a {conservative['name']}"})
                    if rule and rule.multiplier > 1:
                        acts.append({"type": "multiplier", "value": 1.0, "label": "Multiplicador ×1"})
                    add("warn", "DD_HIGH", f"Drawdown al {pct:.0f} %: quedan {room:,.0f} $", "Ya no hay margen para dos días malos: baja tamaño o pásala a un perfil conservador.", acts)
            if rule and rule.enabled and rule.multiplier > 1 and pct is not None and pct >= 40:
                add("warn", "MULT_HIGH_DD", f"Multiplicador ×{rule.multiplier:g} con el drawdown al {pct:.0f} %",
                    "Copia más contratos que la maestra con la mitad del colchón gastado.", [{"type": "multiplier", "value": 1.0, "label": "Multiplicador ×1"}])
            if lim is not None and lim.max_daily_loss > 0 and a.net_pnl <= -0.7 * lim.max_daily_loss and not lim.trading_halted:
                add("warn", "DAILY_LOSS_NEAR", f"P&L del día {a.net_pnl:,.0f} $: al {(-a.net_pnl / lim.max_daily_loss) * 100:.0f} % de la pérdida diaria",
                    "Si sigue así el engine la pausará; mejor pararla tú antes.", [{"type": "pause", "label": "Pausar la copia"}] if rule and not is_master else [])
            if lim is not None and lim.profit_goal > 0 and lim.start_balance > 0:
                gained = self.risk.goal_progress(lim, a)
                if gained is not None and gained >= 0.8 * lim.profit_goal and not lim.trading_halted:
                    add("ok", "GOAL_NEAR", f"Al {(gained / lim.profit_goal) * 100:.0f} % del objetivo de la evaluación",
                        "Falta poco: no lo arriesgues con tamaño grande; el corte del día o el objetivo la cerrarán solos al llegar.", [])
            trades = self._today_trades(acc)
            if trades:
                streak = 0
                for t in sorted(trades, key=lambda t: t["closed_at"], reverse=True):
                    net = float(t.get("pnl") or 0) - float(t.get("commissions") or 0)
                    if net < 0:
                        streak += 1
                    else:
                        break
                if streak >= 3:
                    add("warn", "LOSING_STREAK", f"{streak} operaciones perdedoras seguidas hoy", "Suele ser el momento de parar o bajar tamaño, no de recuperar.",
                        [{"type": "pause", "label": "Pausar la copia"}] if rule and not is_master else [])
            cons = self._consistency(acc)
            thr = CONSISTENCY.get((a.firm or "").lower())
            if cons and thr and cons[1] > 0 and cons[0] / cons[1] > thr:
                add("info", "CONSISTENCY", f"Regla de consistencia: el mejor día es el {cons[0] / cons[1] * 100:.0f} % del total (máx. {thr * 100:.0f} %)",
                    "Para retirar, el resto de días tiene que crecer; mantén tamaño constante.", [])
            if lim is not None and lim.trading_halted:
                add("info", "PAUSED", f"Pausada por riesgo ({lim.halted_reason or 'manual'})", "Se reanuda en Riesgo cuando vuelva a tener margen.", [{"type": "resume", "label": "Reanudar"}] if lim.halted_reason not in ("drawdown",) else [])
            if not rule and not is_master and master:
                add("info", "NO_RULE", "No copia a la maestra", "Vincúlala si quieres que siga a la maestra.", [{"type": "link", "label": "Vincular"}])
        order = {"bad": 0, "warn": 1, "ok": 2, "info": 3}
        advice.sort(key=lambda x: (order[x["severity"]], x["account_id"]))
        counts = {k: sum(1 for x in advice if x["severity"] == k) for k in order}
        return {"advice": advice, "counts": counts, "generated_at": self.now().astimezone().isoformat(timespec="seconds")}
