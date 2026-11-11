"""Gestor preventivo por cuenta (el "CRO" de la consola): cada cuenta con su dueño, su prop firm y sus reglas, vigiladas
ANTES de que la firma las aplique. Para cada cuenta calcula un semáforo (verde / amarillo / rojo) con la causa, el valor,
el límite, el margen y la acción, distinguiendo regla oficial de la firma y límite preventivo propio:

- **drawdown**: equity, suelo de liquidación, margen, % consumido, suelo bloqueado o no; rojo dentro del colchón.
- **MAE** (pérdida flotante máxima): la pérdida flotante no puede pasar del `mae_pct` (30 %) del beneficio de referencia al
  empezar el día (saldo al abrir − saldo inicial); amarillo a partir del `mae_prevent_pct` (20 %, preventivo). Opcional:
  cerrar la posición al superar el oficial (`auto_flatten_mae`).
- **consistencia**: cuánto puede ganar hoy sin que el mejor día pase del X % (de PropRulesService).
- **contratos**: abiertos (equivalente NQ: MNQ = 0,1) frente al tope de la cuenta.
- **retiro**: elegible, disponible, qué falta, retiro pendiente (se descuenta del margen).
- **inactividad**: días desde la última operación frente a `inactivity_days`.
- **pausas / desincronización / desconexión**.

`portfolio()` consolida por dueño o en total: equity, margen, flotante, exposición en NQ equivalentes y el impacto de una
caída de 10/20/30/50 puntos del NQ (qué cuentas tocarían su suelo). `tick()` tras cada sincronización audita lo rojo una
vez al día (GUARDIAN_RED, MAE_WARN, MAE_BREACH) y, con `auto_flatten_mae`, cierra. `report()` resume para el cierre del día
con el veredicto: OPERATIVA HABILITADA SEGÚN EL PLAN / CON RIESGO ELEVADO / RECOMENDACIÓN DE NO OPERAR.
"""
import json
from datetime import date, datetime

from loguru import logger

from tradepilot.domain.symbols import symbol_root as sym_root, point_value

NQ_EQUIV = {"NQ": 1.0, "MNQ": 0.1, "ES": 2.5, "MES": 0.25, "YM": 0.25, "MYM": 0.025, "RTY": 0.25, "M2K": 0.025}   # puntos de NQ equivalentes por punto propio (aprox., para el choque)
POINT_VALUE = {"NQ": 20.0, "MNQ": 2.0, "ES": 50.0, "MES": 5.0, "YM": 5.0, "MYM": 0.5, "RTY": 50.0, "M2K": 5.0, "CL": 1000.0, "MCL": 100.0, "GC": 100.0, "MGC": 10.0}
SHOCKS = (10, 20, 30, 50)
DEFAULT = {"mae_pct": 0.30, "mae_prevent_pct": 0.20, "mae_floor_usd": 0.0, "auto_flatten_mae": False, "rr_max": 5.0, "inactivity_days": 30}
VERDICT = {"green": "OPERATIVA HABILITADA SEGÚN EL PLAN", "amber": "OPERATIVA CON RIESGO ELEVADO", "red": "RECOMENDACIÓN DE NO OPERAR"}
APEX_SRC = "https://apextraderfunding.com/help-center/legacy-payouts/legacy-performance-account-pa-trading-rules/"


class GuardianService:
    def __init__(self, store, accounts, risk, proprules, audit, bridge=None) -> None:
        self.store, self.accounts, self.risk, self.proprules, self.audit, self.bridge = store, accounts, risk, proprules, audit, bridge
        self.now = datetime.now
        self.config = dict(DEFAULT)
        raw = store.get_kv("guardian.config") if store else None
        if raw:
            try:
                self.config.update({k: v for k, v in json.loads(raw).items() if k in self.config})
            except ValueError:
                pass
        self._noted: dict[tuple, str] = {}        # (cuenta, código) -> día ya avisado

    def set_config(self, **changes) -> dict:
        for k, v in changes.items():
            if k in self.config and v is not None:
                self.config[k] = type(DEFAULT[k])(v)
        if self.store:
            self.store.set_kv("guardian.config", json.dumps(self.config))
        return self.config

    # ---- por cuenta ----
    def _last_trade_day(self, acc: str) -> str | None:
        if not self.store:
            return None
        try:
            from tradepilot.core.scope import stage_bypass
            with stage_bypass():
                rows = self.store.get_trades("0000-00-00", "9999-12-31", acc)
        except Exception:
            return None
        return max((r["day"] for r in rows), default=None)

    def _live(self, snap) -> bool:
        """El gestor solo vigila cuentas activas (habilitadas, conectadas y con saldo): las demás no copian ni tienen qué proteger."""
        if self.risk is not None and hasattr(self.risk, "is_live"):
            return self.risk.is_live(snap)
        return bool(snap is not None and snap.enabled and snap.reported)

    def evaluate(self, account_id: str) -> dict | None:
        snap = self.accounts.find(account_id) if self.accounts is not None else None
        if snap is None:
            return None
        cfg = self.config
        lim = self.risk.limits.get(snap.account_id) if self.risk is not None else None
        alerts: list[dict] = []

        def add(level, code, cause, value, limit, margin, action, source="preventivo"):
            alerts.append({"level": level, "code": code, "cause": cause, "value": value, "limit": limit, "margin": margin, "action": action, "source": source})

        dd = snap.drawdown
        equity = snap.balance + snap.unrealized_pnl
        floating = snap.unrealized_pnl
        # 1) drawdown / liquidación
        if dd.floor is not None and dd.room is not None:
            buf = float(lim.drawdown_buffer) if lim else 0.0
            if dd.room <= max(buf, 0):
                add("red", "LIQUIDATION_NEAR", "a menos del colchón del suelo de liquidación", round(dd.room, 2), round(buf, 2), round(dd.room - buf, 2), "no abrir nada; cerrar lo abierto y revisar el límite", "oficial: trailing drawdown de la firma")
            elif (dd.pct or 0) >= 80:
                add("amber", "DRAWDOWN_HIGH", f"drawdown al {dd.pct:.0f} %", round(dd.drawdown, 2), round(dd.limit, 2), round(dd.room, 2), "mitad de tamaño; nada de añadir a perdedoras", "oficial: trailing drawdown de la firma")
        elif lim is None or lim.max_trailing_drawdown <= 0:
            add("amber", "NO_DRAWDOWN_RULE", "sin drawdown configurado: no se puede vigilar la liquidación", None, None, None, "pon el perfil de prop firm (Cuentas → Prop firm)")
        # 2) MAE: pérdida flotante frente al beneficio de referencia al abrir el día
        start = float(lim.start_balance) if lim and lim.start_balance > 0 else float(snap.plan_size or 0)
        day_start_balance = snap.balance - (snap.realized_pnl or 0.0)
        ref_profit = round(day_start_balance - start, 2) if start > 0 else None
        mae = {"reference_profit": ref_profit, "limit": None, "floating": round(min(0.0, floating), 2), "pct": None, "evaluable": ref_profit is not None}
        if ref_profit is not None:
            limit_usd = max(cfg["mae_pct"] * max(ref_profit, 0.0), float(cfg["mae_floor_usd"] or 0))
            mae["limit"] = round(limit_usd, 2)
            loss = max(0.0, -floating)
            if limit_usd > 0:
                mae["pct"] = round(100 * loss / limit_usd, 1)
                if loss >= limit_usd:
                    add("red", "MAE_BREACH", f"pérdida flotante {loss:,.0f} $ supera el {cfg['mae_pct'] * 100:.0f} % del beneficio de referencia ({ref_profit:,.0f} $)", round(loss, 2), round(limit_usd, 2), round(limit_usd - loss, 2), "cerrar o reducir ya la posición", f"oficial: regla MAE 30 % ({APEX_SRC})")
                elif loss >= cfg["mae_prevent_pct"] / cfg["mae_pct"] * limit_usd:
                    add("amber", "MAE_WARN", f"pérdida flotante {loss:,.0f} $ ya es el {100 * loss / limit_usd:.0f} % del límite MAE", round(loss, 2), round(limit_usd, 2), round(limit_usd - loss, 2), "no promediar; stop más cerca o cerrar parte", "preventivo (límite propio por debajo del oficial)")
            elif loss > 0 and ref_profit <= 0:
                add("amber", "MAE_NO_REFERENCE", "sin beneficio de referencia hoy: cualquier pérdida flotante cuenta contra el drawdown directamente", round(loss, 2), 0.0, None, "tamaño mínimo hasta tener colchón de beneficio", "oficial: regla MAE 30 %")
        # 3) consistencia y retiro (PropRulesService)
        pr = None
        try:
            pr = self.proprules.evaluate(snap.account_id) if self.proprules is not None else None
        except Exception as exc:
            logger.warning(f"Gestor preventivo: reglas de {snap.account_id}: {exc}")
        cons = None
        if pr and pr.get("max_today") is not None and pr["max_today"] > 0:
            thr = (pr["eval_rule"]["consistency"] if pr["is_eval"] and pr.get("eval_rule") else pr["rule"]["consistency"]) * 100
            cons = {"today": pr["today"], "max_today": pr["max_today"], "room": pr["today_room"], "best_share": pr["best_day_share"], "thr": thr}
            if pr["today"] >= pr["max_today"]:
                add("red", "CONSISTENCY_OVER", f"hoy {pr['today']:+,.0f} $ supera el máximo {pr['max_today']:,.0f} $ que permite la consistencia del {thr:.0f} %", round(pr["today"], 2), round(pr["max_today"], 2), round(pr["today_room"], 2), "parar por hoy: el resto de días tendrá que crecer antes del retiro", "oficial: regla de consistencia")
            elif pr["today"] >= 0.8 * pr["max_today"]:
                add("amber", "CONSISTENCY_NEAR", f"hoy {pr['today']:+,.0f} $, al {100 * pr['today'] / pr['max_today']:.0f} % del máximo de consistencia", round(pr["today"], 2), round(pr["max_today"], 2), round(pr["today_room"], 2), "bajar tamaño o parar por hoy", "oficial: regla de consistencia")
        elif pr and pr.get("best_day_share") is not None:
            cons = {"today": pr["today"], "max_today": None, "room": None, "best_share": pr["best_day_share"], "thr": None}
        payout = None
        pending_amt = 0.0
        if pr is not None:
            try:
                pending_amt = sum(float(c["amount"] or 0) for c in (self.store.list_cash_events(snap.account_id) if self.store else []) if c["kind"] == "withdrawal" and c["status"] == "pending")
            except Exception:
                pending_amt = 0.0
            payout = {"eligible": pr["eligible"], "available": pr["available"], "n_payouts": pr["n_payouts"], "is_eval": pr["is_eval"], "status": pr.get("status"),
                      "missing": [x["label"] + (f" (faltan {x['missing']:,.0f})" if isinstance(x.get("missing"), (int, float)) and x["missing"] else "") for x in pr["requirements"] if x["status"] != "ok"],
                      "pending": round(pending_amt, 2)}
            if pending_amt > 0 and dd.room is not None:
                add("amber", "PAYOUT_PENDING", f"retiro pendiente de {pending_amt:,.0f} $: el margen real es {dd.room - pending_amt:,.0f} $", round(pending_amt, 2), round(dd.room, 2), round(dd.room - pending_amt, 2), "operar como si ya estuviera descontado")
        # 4) contratos
        open_eq = 0.0
        open_contracts = 0
        for p in snap.open_positions:
            if p.quantity:
                open_contracts += abs(int(p.quantity))
                open_eq += p.quantity * NQ_EQUIV.get(sym_root(p.symbol), 1.0)
        maxc = int(lim.max_position_size) if lim else 0
        if maxc and open_contracts > maxc:
            add("red", "CONTRACTS_OVER", f"{open_contracts} contratos abiertos, tope {maxc}", open_contracts, maxc, maxc - open_contracts, "reducir a mercado hasta el tope", "oficial: contratos máximos de la firma")
        elif maxc and open_contracts >= 0.8 * maxc and open_contracts > 0:
            add("amber", "CONTRACTS_NEAR", f"{open_contracts} de {maxc} contratos", open_contracts, maxc, maxc - open_contracts, "no añadir")
        # 5) pausas, desincronización, conexión
        if lim and lim.trading_halted:
            lvl = "red" if lim.halted_reason in ("drawdown", "daily_loss") else "amber"
            add(lvl, "HALTED", f"cuenta pausada ({lim.halted_reason or 'manual'})", None, None, None, "ver Inicio → Cuentas en pausa")
        if snap.desync:
            add("amber", "DESYNC", "desincronizada con la maestra", None, None, None, "igualar o cerrar en Cuentas")
        if snap.connected is False:
            add("amber", "DISCONNECTED", "desconectada en NinjaTrader", None, None, None, "reconectar antes de operar")
        # 6) inactividad
        last_day = self._last_trade_day(snap.account_id)
        inactive = (date.today() - date.fromisoformat(last_day)).days if last_day else None
        lim_days = int(cfg["inactivity_days"])
        if inactive is not None and inactive >= lim_days - 2:
            add("red" if inactive >= lim_days else "amber", "INACTIVITY", f"{inactive} días sin operar (política de inactividad: {lim_days})", inactive, lim_days, lim_days - inactive, "hacer una operación pequeña para mantenerla activa", "oficial: política de inactividad")
        status = "red" if any(a["level"] == "red" for a in alerts) else "amber" if any(a["level"] == "amber" for a in alerts) else "green"
        return {"account_id": snap.account_id, "alias": snap.alias, "owner": snap.owner, "firm": snap.firm, "plan": snap.plan, "enabled": snap.enabled,
                "balance": round(snap.balance, 2), "equity": round(equity, 2), "floating": round(floating, 2), "net_pnl": round(snap.net_pnl, 2),
                "floor": dd.floor, "room": dd.room, "dd_pct": dd.pct, "dd_locked": dd.locked, "peak": dd.peak, "mae": mae, "consistency": cons,
                "contracts": {"open": open_contracts, "max": maxc, "nq_equiv": round(open_eq, 2)}, "payout": payout, "last_trade_day": last_day, "inactive_days": inactive,
                "status": status, "alerts": alerts}

    # ---- cartera ----
    def portfolio(self, owner: str | None = None) -> dict:
        rows = []
        for a in (self.accounts.accounts.values() if self.accounts is not None else []):
            if not self._live(a) or (owner and (a.owner or "").lower() != owner.lower()):
                continue
            r = self.evaluate(a.account_id)
            if r:
                rows.append(r)
        rows.sort(key=lambda r: ({"red": 0, "amber": 1, "green": 2}[r["status"]], r["owner"], r["account_id"]))
        shocks = []
        for pts in SHOCKS:
            total = 0.0
            hit = []
            for r in rows:
                snap = self.accounts.find(r["account_id"])
                change = 0.0        # variación del P&L de la cuenta si el NQ cae `pts` puntos (largas pierden, cortas ganan)
                for p in (snap.open_positions if snap else []):
                    rt = sym_root(p.symbol)
                    move = pts * NQ_EQUIV.get(rt, 1.0) if rt in NQ_EQUIV else 0.0   # instrumentos no ligados al NQ: sin impacto en este choque
                    change += -p.quantity * move * (POINT_VALUE.get(rt) or point_value(p.symbol) or 20.0)
                total += change
                if r["room"] is not None and change < 0 and r["room"] + change <= 0:
                    hit.append(r["account_id"])
            shocks.append({"points": pts, "pnl": round(total, 2), "liquidated": hit})
        counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("green", "amber", "red")}
        owners = sorted({(a.owner or "") for a in (self.accounts.accounts.values() if self.accounts is not None else []) if a.enabled})
        return {"accounts": rows, "counts": counts, "owners": [o for o in owners if o], "unassigned": sum(1 for a in (self.accounts.accounts.values() if self.accounts is not None else []) if a.enabled and not a.owner),
                "totals": {"equity": round(sum(r["equity"] for r in rows), 2), "room": round(sum(r["room"] or 0 for r in rows), 2), "floating": round(sum(r["floating"] for r in rows), 2),
                           "net_pnl": round(sum(r["net_pnl"] for r in rows), 2), "nq_equiv": round(sum(r["contracts"]["nq_equiv"] for r in rows), 2), "open_contracts": sum(r["contracts"]["open"] for r in rows)},
                "shocks": shocks, "config": self.config, "verdict": self.verdict(counts), "generated_at": self.now().astimezone().isoformat(timespec="seconds")}

    @staticmethod
    def verdict(counts: dict) -> str:
        if counts.get("red"):
            return VERDICT["red"]
        if counts.get("amber"):
            return VERDICT["amber"]
        return VERDICT["green"]

    # ---- vigilancia tras cada sincronización ----
    async def tick(self) -> None:
        today = self.now().strftime("%Y-%m-%d")
        for a in list(self.accounts.accounts.values() if self.accounts is not None else []):
            if not self._live(a):
                continue
            r = self.evaluate(a.account_id)
            if not r:
                continue
            for al in r["alerts"]:
                if al["code"] in ("HALTED", "DESYNC", "DISCONNECTED", "NO_DRAWDOWN_RULE"):
                    continue        # ya tienen su propio aviso
                key = (a.account_id, al["code"])
                if al["level"] == "red" and self._noted.get(key) != today:
                    self._noted[key] = today
                    ev = "MAE_BREACH" if al["code"] == "MAE_BREACH" else "GUARDIAN_RED"
                    self.audit.log(ev, f"{a.account_id}: {al['cause']}. Acción: {al['action']}. ({al['source']})", target=a.account_id, details=al)
                    if al["code"] == "MAE_BREACH" and self.config["auto_flatten_mae"] and self.risk is not None:
                        try:
                            await self.risk.flatten(a.account_id, "MAE superado (gestor preventivo)")
                        except Exception as exc:
                            self.audit.log("ERROR", f"{a.account_id}: no se pudo cerrar tras superar el MAE: {exc}", target=a.account_id)
                elif al["level"] == "amber" and al["code"] == "MAE_WARN" and self._noted.get(key) != today:
                    self._noted[key] = today
                    self.audit.log("MAE_WARN", f"{a.account_id}: {al['cause']}. {al['action']}.", target=a.account_id, details=al)

    # ---- informe ----
    def report(self, owner: str | None = None) -> dict:
        pf = self.portfolio(owner)
        lines = []
        for r in pf["accounts"]:
            ico = {"green": "🟢", "amber": "🟡", "red": "🔴"}[r["status"]]
            parts = [f"{ico} {r['alias'] or r['account_id']}" + (f" ({r['owner']})" if r["owner"] else ""), f"neto {r['net_pnl']:+,.0f} $", f"margen {r['room']:,.0f} $" if r["room"] is not None else "sin drawdown"]
            if r["mae"]["pct"] is not None:
                parts.append(f"MAE {r['mae']['pct']:.0f} %")
            if r["consistency"] and r["consistency"]["max_today"]:
                parts.append(f"consistencia: hoy máx. {r['consistency']['max_today']:,.0f} $")
            if r["alerts"]:
                parts.append("; ".join(a["cause"] for a in r["alerts"][:2]))
            lines.append(" · ".join(parts))
        return {"lines": lines, "verdict": pf["verdict"], "counts": pf["counts"], "totals": pf["totals"]}
