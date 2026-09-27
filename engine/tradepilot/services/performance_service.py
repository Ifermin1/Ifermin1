"""Rendimiento por día y por operación, para el calendario de la consola.

- Operaciones cerradas: se reconstruyen a partir de los fills que ve el engine (maestra, seguidoras y manuales) llevando
  la posición de cada cuenta por raíz de símbolo: entradas, salidas parciales y giros. Cada ida y vuelta completa es una
  operación con precio medio de entrada y salida, P&L (puntos × valor del punto) y comisiones estimadas.
- P&L diario del bróker: lo que NinjaTrader reporta para el día (realizado + flotante), guardado por cuenta y día en cada
  sincronización. En el calendario manda el del bróker cuando existe; la suma de operaciones es el respaldo.
"""
import random
from datetime import datetime, timedelta

from loguru import logger

from tradepilot.domain.symbols import point_value, symbol_root
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore


class PerformanceService:
    def __init__(self, store: SQLiteStore | None, commissions=None, eod_time: str = "17:00") -> None:
        self.store = store
        self.commissions = commissions
        self.eod_time = eod_time
        self.now = datetime.now
        self._open: dict[tuple[str, str], dict] = {}     # (cuenta, raíz) -> posición abierta en curso
        self._seeded = False
        self._last_daily: dict[tuple[str, str], tuple[float, float]] = {}   # (cuenta, día) -> (pnl, monotonic)
        self._last_balance: dict[tuple[str, str], tuple[float, float]] = {}

    # ---- día de trading: la sesión que abre a la hora de cierre (17:00) pertenece al día siguiente, como en CME.
    # (Las comisiones diarias se guardan por la fecha en que ARRANCA la sesión: un día menos; month() lo alinea.) ----
    def day_key(self, at: datetime | None = None) -> str:
        at = at or self.now()
        day = at.date() + timedelta(days=1) if at.strftime("%H:%M") >= self.eod_time else at.date()
        return day.isoformat()

    def _fee(self, symbol: str, contracts: int) -> float:
        if self.commissions is None or not self.commissions.config.enabled:
            return 0.0
        return round(self.commissions.rate(symbol) * contracts, 2)

    # ---- posiciones abiertas antes de arrancar ----
    def seed_open_positions(self, positions: list) -> None:
        """La primera foto de posiciones del bróker: lo que ya estaba abierto cuenta como posición en curso (sin hora de
        entrada) para que su cierre salga como operación y no como una entrada al revés."""
        if self._seeded:
            return
        self._seeded = True
        for p in positions:
            if not p.quantity:
                continue
            key = (p.account_id, symbol_root(p.symbol))
            self._open[key] = {"side": 1 if p.quantity > 0 else -1, "qty": abs(int(p.quantity)), "avg": float(p.avg_price or 0.0),
                               "symbol": p.symbol, "opened_at": None, "max_qty": abs(int(p.quantity)), "fills": 0,
                               "realized": 0.0, "closed_qty": 0, "exit_avg": 0.0, "fees": 0.0}

    # ---- fills ----
    def note_fill(self, account: str, symbol: str, action: str, quantity: int, price: float, at: datetime | None = None) -> dict | None:
        """Aplica un fill a la posición en curso de esa cuenta/raíz. Devuelve la operación si el fill la cierra del todo."""
        if quantity <= 0 or not account or not symbol:
            return None
        at = at or self.now()
        sign = -1 if action.upper().startswith("SELL") else 1
        key = (account, symbol_root(symbol))
        pos = self._open.get(key)
        closed: dict | None = None
        if pos is None or pos["qty"] == 0:
            self._open[key] = self._new_pos(sign, quantity, price, symbol, at)
            return None
        if pos["side"] == sign:
            pos["avg"] = (pos["avg"] * pos["qty"] + price * quantity) / (pos["qty"] + quantity)
            pos["qty"] += quantity
            pos["max_qty"] = max(pos["max_qty"], pos["qty"])
            pos["fills"] += 1
            pos["fees"] += self._fee(symbol, quantity)
            return None
        close_qty = min(quantity, pos["qty"])
        pos["realized"] += (price - pos["avg"]) * close_qty * point_value(symbol) * pos["side"]
        pos["exit_avg"] = (pos["exit_avg"] * pos["closed_qty"] + price * close_qty) / (pos["closed_qty"] + close_qty)
        pos["closed_qty"] += close_qty
        pos["qty"] -= close_qty
        pos["fills"] += 1
        pos["fees"] += self._fee(symbol, close_qty)
        if pos["qty"] == 0:
            closed = self._close(account, pos, at)
            del self._open[key]
        if quantity > close_qty:       # giro: lo que sobra abre posición en el otro sentido
            self._open[key] = self._new_pos(sign, quantity - close_qty, price, symbol, at)
        return closed

    def _new_pos(self, sign: int, qty: int, price: float, symbol: str, at: datetime) -> dict:
        return {"side": sign, "qty": qty, "avg": price, "symbol": symbol, "opened_at": at, "max_qty": qty, "fills": 1,
                "realized": 0.0, "closed_qty": 0, "exit_avg": 0.0, "fees": self._fee(symbol, qty)}

    def _close(self, account: str, pos: dict, at: datetime) -> dict:
        trade = {"account_id": account, "day": self.day_key(at), "symbol": pos["symbol"], "side": "long" if pos["side"] > 0 else "short",
                 "quantity": int(pos["max_qty"]), "entry_price": round(pos["avg"], 6), "exit_price": round(pos["exit_avg"], 6),
                 "pnl": round(pos["realized"], 2), "commissions": round(pos["fees"], 2),
                 "opened_at": pos["opened_at"].isoformat(timespec="seconds") if pos["opened_at"] else None,
                 "closed_at": at.isoformat(timespec="seconds"), "fills": int(pos["fills"])}
        if self.store:
            try:
                trade["id"] = self.store.save_trade(trade)
            except Exception as exc:
                logger.warning(f"No se pudo guardar la operación de {account}: {exc}")
        return trade

    def open_positions(self) -> dict[tuple[str, str], dict]:
        return self._open

    # ---- P&L diario del bróker ----
    def note_daily_pnl(self, account: str, pnl: float, at: datetime | None = None) -> None:
        """Guarda el P&L del día que reporta el bróker (como mucho una vez cada 20 s por cuenta, o si cambia)."""
        if not self.store:
            return
        at = at or self.now()
        day = self.day_key(at)
        import time as _t
        last = self._last_daily.get((account, day))
        if last is not None and abs(last[0] - pnl) < 0.005 and _t.monotonic() - last[1] < 20:
            return
        self._last_daily[(account, day)] = (pnl, _t.monotonic())
        try:
            self.store.save_daily_pnl(account, day, round(pnl, 2), at.isoformat(timespec="seconds"))
        except Exception as exc:
            logger.warning(f"No se pudo guardar el P&L diario de {account}: {exc}")

    def note_balance(self, account: str, balance: float, at: datetime | None = None) -> None:
        """Saldo que reporta el bróker, guardado por cuenta y día (manda el último del día: es el saldo de cierre)."""
        if not self.store or balance is None:
            return
        at = at or self.now()
        day = self.day_key(at)
        import time as _t
        last = self._last_balance.get((account, day))
        if last is not None and abs(last[0] - float(balance)) < 0.005 and _t.monotonic() - last[1] < 60:
            return
        self._last_balance[(account, day)] = (float(balance), _t.monotonic())
        try:
            self.store.save_balance_day(account, day, round(float(balance), 2), at.isoformat(timespec="seconds"))
        except Exception as exc:
            logger.warning(f"No se pudo guardar el saldo diario de {account}: {exc}")

    # ---- consulta: análisis de un periodo (duración, riesgo, balance y retiros) ----
    def analysis(self, day_from: str, day_to: str, account: str | None = None) -> dict:
        """Operaciones del periodo, filas por día (operaciones, bruto, comisiones, P&L del bróker, saldo de cierre) y
        movimientos de caja de la cuenta. Los cálculos de duración y riesgo los hace la consola con estos datos."""
        if not self.store:
            return {"from": day_from, "to": day_to, "trades": [], "days": [], "cash": [], "accounts": [], "fills": 0, "balances": {}}
        trades = self.store.get_trades(day_from, day_to, account)
        broker = self.store.get_daily_pnl(day_from, day_to, account)
        balances = self.store.get_balance_days(day_from, day_to, account)
        prev = lambda d: (datetime.fromisoformat(d).date() - timedelta(days=1)).isoformat()
        nxt = lambda d: (datetime.fromisoformat(d).date() + timedelta(days=1)).isoformat()
        fees = [{**f, "day": nxt(f["day"])} for f in self.store.get_commissions_range(prev(day_from), prev(day_to), account)]
        cash = [c for c in self.store.list_cash_events(account) if day_from <= c["day"] <= day_to]
        days: dict[str, dict] = {}

        def day(d: str) -> dict:
            return days.setdefault(d, {"day": d, "trades": 0, "pnl_trades": 0.0, "pnl_broker": None, "commissions": 0.0, "contracts": 0,
                                       "balance": None, "withdrawals": 0.0, "deposits": 0.0})
        for t in trades:
            x = day(t["day"])
            x["trades"] += 1
            x["pnl_trades"] = round(x["pnl_trades"] + float(t["pnl"] or 0.0), 2)
        for b in broker:
            x = day(b["day"])
            x["pnl_broker"] = round((x["pnl_broker"] or 0.0) + float(b["pnl"] or 0.0), 2)
        enabled = self.commissions is None or self.commissions.config.enabled
        for f in fees:
            x = day(f["day"])
            x["contracts"] += int(f["contracts"] or 0)
            x["commissions"] = round(x["commissions"] + (float(f["cost"] or 0.0) if enabled else 0.0), 2)
        per_day_balance: dict[str, float] = {}
        for b in balances:            # varias cuentas: suma de saldos de cierre del día
            per_day_balance[b["day"]] = round(per_day_balance.get(b["day"], 0.0) + float(b["balance"] or 0.0), 2)
        for d, v in per_day_balance.items():
            day(d)["balance"] = v
        for c in cash:
            x = day(c["day"])
            if c["kind"] == "withdrawal" and c["status"] != "rejected":
                x["withdrawals"] = round(x["withdrawals"] + float(c["amount"] or 0.0), 2)
            elif c["kind"] == "deposit" and c["status"] != "rejected":
                x["deposits"] = round(x["deposits"] + float(c["amount"] or 0.0), 2)
        accounts = sorted({t["account_id"] for t in trades} | {b["account_id"] for b in broker} | {b["account_id"] for b in balances})
        return {"from": day_from, "to": day_to, "trades": trades, "days": sorted(days.values(), key=lambda x: x["day"]), "cash": cash,
                "accounts": accounts, "fills": self.store.count_fills(day_from, day_to, account)}

    # ---- consulta: un mes para el calendario ----
    def month(self, year: int, month: int, account: str | None = None) -> dict:
        first = datetime(year, month, 1).date()
        last = (datetime(year + (month == 12), (month % 12) + 1, 1).date() - timedelta(days=1))
        d0, d1 = first.isoformat(), last.isoformat()
        if not self.store:
            return {"from": d0, "to": d1, "days": [], "trades": [], "accounts": []}
        trades = self.store.get_trades(d0, d1, account)
        broker = self.store.get_daily_pnl(d0, d1, account)
        prev = lambda d: (datetime.fromisoformat(d).date() - timedelta(days=1)).isoformat()
        nxt = lambda d: (datetime.fromisoformat(d).date() + timedelta(days=1)).isoformat()
        fees = [{**f, "day": nxt(f["day"])} for f in self.store.get_commissions_range(prev(d0), prev(d1), account)]
        accounts = sorted({t["account_id"] for t in trades} | {b["account_id"] for b in broker} | {f["account_id"] for f in fees})
        days: dict[str, dict] = {}

        def day(d: str) -> dict:
            return days.setdefault(d, {"day": d, "pnl_broker": None, "pnl_trades": 0.0, "trades": 0, "wins": 0, "losses": 0,
                                       "best": 0.0, "worst": 0.0, "commissions": 0.0, "contracts": 0, "accounts": {}})
        for t in trades:
            x = day(t["day"])
            x["pnl_trades"] = round(x["pnl_trades"] + t["pnl"], 2)
            x["trades"] += 1
            x["wins"] += 1 if t["pnl"] > 0 else 0
            x["losses"] += 1 if t["pnl"] < 0 else 0
            x["best"] = max(x["best"], t["pnl"])
            x["worst"] = min(x["worst"], t["pnl"])
            a = x["accounts"].setdefault(t["account_id"], {"pnl_trades": 0.0, "trades": 0, "pnl_broker": None})
            a["pnl_trades"] = round(a["pnl_trades"] + t["pnl"], 2)
            a["trades"] += 1
        for b in broker:
            x = day(b["day"])
            x["pnl_broker"] = round((x["pnl_broker"] or 0.0) + float(b["pnl"] or 0.0), 2)
            x["accounts"].setdefault(b["account_id"], {"pnl_trades": 0.0, "trades": 0, "pnl_broker": None})["pnl_broker"] = float(b["pnl"] or 0.0)
        enabled = self.commissions is None or self.commissions.config.enabled
        for f in fees:
            x = day(f["day"])
            x["contracts"] += int(f["contracts"] or 0)
            x["commissions"] = round(x["commissions"] + (float(f["cost"] or 0.0) if enabled else 0.0), 2)
        for x in days.values():
            x["pnl"] = x["pnl_broker"] if x["pnl_broker"] is not None else x["pnl_trades"]
            x["net"] = round(x["pnl"] - x["commissions"], 2)
        return {"from": d0, "to": d1, "days": sorted(days.values(), key=lambda x: x["day"]), "trades": trades, "accounts": accounts}

    # ---- demo ----
    def seed_demo(self, accounts: list[str], days: int = 45, end_balances: dict[str, float] | None = None) -> int:
        """Modo simulador: un mes y medio de operaciones inventadas (con deriva positiva) si no hay ninguna guardada. El
        saldo diario se reconstruye hacia atrás desde el saldo actual del simulador para que la curva enlace con hoy."""
        if not self.store or self.store.count_trades() > 0:
            return 0
        rng = random.Random(7)
        n = 0
        today = self.now().date()
        history: dict[str, list[tuple[str, float, float]]] = {acc: [] for acc in accounts}   # cuenta -> (día, neto, retiro)
        withdrawals_done = 0
        running = {acc: 0.0 for acc in accounts}
        for back in range(days, 0, -1):
            d = today - timedelta(days=back)
            if d.weekday() >= 5 or rng.random() < 0.12:
                continue
            for acc in accounts:
                total = 0.0
                for k in range(rng.randint(1, 6)):
                    win = rng.random() < 0.56
                    pts = round(rng.uniform(4, 40) if win else -rng.uniform(4, 30), 2)
                    qty = rng.choice([1, 1, 2, 2, 3])
                    sym = "NQ 12-26"
                    entry = round(rng.uniform(19500, 20500) * 4) / 4
                    side = rng.choice(["long", "short"])
                    exit_ = entry + pts if side == "long" else entry - pts
                    opened = datetime(d.year, d.month, d.day, rng.randint(8, 14), rng.randint(0, 59), rng.randint(0, 59))
                    closed = opened + timedelta(minutes=rng.randint(2, 90))
                    pnl = round(pts * qty * point_value(sym), 2)
                    self.store.save_trade({"account_id": acc, "day": d.isoformat(), "symbol": sym, "side": side, "quantity": qty,
                                           "entry_price": entry, "exit_price": round(exit_, 2), "pnl": pnl, "commissions": round(2 * 2.0 * qty, 2),
                                           "opened_at": opened.isoformat(timespec="seconds"), "closed_at": closed.isoformat(timespec="seconds"), "fills": 2})
                    total += pnl
                    n += 1
                    if self.commissions is not None and self.commissions.store:
                        c, cost = self.store.get_commissions(d.isoformat()).get(acc, (0, 0.0))
                        self.store.save_commission(acc, d.isoformat(), c + 2 * qty, round(cost + 2 * 2.0 * qty, 2))
                self.store.save_daily_pnl(acc, d.isoformat(), round(total + rng.uniform(-5, 5), 2), datetime(d.year, d.month, d.day, 16).isoformat())
                running[acc] += total
                withdrawal = 0.0
                if running[acc] > 3000 and withdrawals_done < 3 and rng.random() < 0.5:
                    withdrawals_done += 1
                    withdrawal = 1500.0 if withdrawals_done < 3 else 1350.0
                    running[acc] -= withdrawal
                    self.store.add_cash_event({"account_id": acc, "day": d.isoformat(), "kind": "withdrawal", "amount": withdrawal,
                                               "paid": round(withdrawal * 0.9, 2), "status": "completed", "note": "Retiro de demostración",
                                               "created_at": datetime(d.year, d.month, d.day, 17).isoformat(timespec="seconds")})
                history[acc].append((d.isoformat(), total, withdrawal))
        for acc, rows in history.items():
            end = float((end_balances or {}).get(acc, 50000.0))
            balance = end - sum(t for _, t, _ in rows) + sum(w for _, _, w in rows)
            for day_, total, withdrawal in rows:
                balance = round(balance + total - withdrawal, 2)
                self.store.save_balance_day(acc, day_, balance, f"{day_}T16:00:00")
        if accounts:
            self.store.add_cash_event({"account_id": accounts[0], "day": today.isoformat(), "kind": "withdrawal", "amount": 1000.0, "paid": None,
                                       "status": "pending", "note": "Solicitud de demostración pendiente",
                                       "created_at": self.now().isoformat(timespec="seconds")})
        logger.info(f"Simulador: {n} operaciones de demostración generadas para el calendario")
        return n
