"""Informe semanal imprimible y exportación CSV de las operaciones.

El informe cubre siete días (por defecto, la semana lunes-domingo del día pedido) por cuenta o por grupo (etiqueta):
resultado por cuenta y por día, operaciones, acierto, factor de beneficio, comisiones, contratos, saldo de cierre, retiros
pedidos y pagos recibidos de la semana (movimientos de caja), lo mejor y lo peor, y las tres reglas del coach. Sale como
HTML autocontenido (sin red) que se imprime o se guarda en PDF desde el navegador.
"""
import csv
import html
import io
from datetime import date, datetime, timedelta

from tradepilot.domain.tradestats import best, brief, by_symbol, by_weekday, net_of, seconds_of, trade_stats, worst


def week_bounds(day: str | None = None) -> tuple[str, str]:
    """Lunes y domingo de la semana a la que pertenece `day` (hoy si no se indica)."""
    d = date.fromisoformat(day) if day else date.today()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def _usd(v: float | None) -> str:
    return "—" if v is None else f"{v:+,.2f} $".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.0f} %"


def _dur(s: float | None) -> str:
    if s is None:
        return "—"
    return f"{int(s)} s" if s < 90 else f"{s / 60:.1f} min" if s < 5400 else f"{s / 3600:.1f} h"


class ReportService:
    def __init__(self, store, accounts=None, coach=None, performance=None) -> None:
        self.store, self.accounts, self.coach, self.performance = store, accounts, coach, performance

    # ---- selección de cuentas ----
    def _label(self, account_id: str) -> str:
        snap = self.accounts.find(account_id) if self.accounts is not None else None
        return (snap.alias or snap.account_id) if snap is not None and snap.alias else account_id

    def accounts_for(self, account: str | None, tag: str | None) -> list[str] | None:
        """None = todas. Con `tag` (grupo) solo las cuentas con esa etiqueta; con `account`, solo esa."""
        if account:
            return [account]
        if tag and self.accounts is not None:
            t = tag.strip().lower()
            return sorted(a.account_id for a in self.accounts.accounts.values() if t in [x.lower() for x in a.tags])
        return None

    # ---- datos ----
    def weekly(self, day: str | None = None, account: str | None = None, tag: str | None = None) -> dict:
        day_from, day_to = week_bounds(day)
        ids = self.accounts_for(account, tag)
        trades = [t for t in self.store.get_trades(day_from, day_to) if ids is None or t["account_id"] in ids]
        cash = [c for c in self.store.list_cash_events() if day_from <= c["day"] <= day_to and (ids is None or c["account_id"] in ids)]
        balances = [b for b in self.store.get_balance_days(day_from, day_to) if ids is None or b["account_id"] in ids]
        prev_from = (date.fromisoformat(day_from) - timedelta(days=1)).isoformat()
        fees = [f for f in self.store.get_commissions_range(prev_from, (date.fromisoformat(day_to) - timedelta(days=1)).isoformat()) if ids is None or f["account_id"] in ids]
        acc_ids = sorted(set(ids or []) | {t["account_id"] for t in trades} | {c["account_id"] for c in cash} | {b["account_id"] for b in balances})
        per_account = []
        for aid in acc_ids:
            ts = [t for t in trades if t["account_id"] == aid]
            st = trade_stats(ts)
            bal = [b for b in balances if b["account_id"] == aid]
            w = [c for c in cash if c["account_id"] == aid and c["kind"] == "withdrawal" and c["status"] != "rejected"]
            per_account.append({"account_id": aid, "label": self._label(aid), **st,
                                "commissions": round(sum(float(t.get("commissions") or 0) for t in ts), 2),
                                "balance_start": bal[0]["balance"] if bal else None, "balance_end": bal[-1]["balance"] if bal else None,
                                "withdrawals": round(sum(float(c["amount"] or 0) for c in w), 2), "paid": round(sum(float(c["paid"] or 0) for c in w if c["paid"] is not None), 2),
                                "pending": sum(1 for c in w if c["status"] == "pending")})
        days = []
        d = date.fromisoformat(day_from)
        while d <= date.fromisoformat(day_to):
            ts = [t for t in trades if t["day"] == d.isoformat()]
            days.append({"day": d.isoformat(), "weekday": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][d.weekday()], **trade_stats(ts)})
            d += timedelta(days=1)
        total = trade_stats(trades)
        coach = None
        if self.coach is not None and trades:
            try:
                coach = self.coach.heuristic_rules(trades)
            except Exception:
                coach = None
        return {"from": day_from, "to": day_to, "generated_at": datetime.now().isoformat(timespec="seconds"), "scope": account or (f"grupo {tag}" if tag else "todas las cuentas"),
                "accounts": per_account, "days": days, "total": {**total, "commissions": round(sum(float(t.get("commissions") or 0) for t in trades), 2),
                                                                 "contracts_fees": sum(int(f["contracts"] or 0) for f in fees),
                                                                 "withdrawals": round(sum(a["withdrawals"] for a in per_account), 2), "paid": round(sum(a["paid"] for a in per_account), 2)},
                "by_symbol": by_symbol(trades), "by_weekday": by_weekday(trades), "best": [brief(t) for t in best(trades, 3)], "worst": [brief(t) for t in worst(trades, 3)],
                "cash": cash, "rules": coach or []}

    # ---- HTML imprimible ----
    def weekly_html(self, data: dict) -> str:
        e = html.escape
        t = data["total"]
        rows_acc = "".join(
            f"<tr><td>{e(a['label'])}{' <small>' + e(a['account_id']) + '</small>' if a['label'] != a['account_id'] else ''}</td><td>{a['n']}</td><td>{_pct(a['win_rate'])}</td>"
            f"<td class='{'pos' if a['net'] > 0 else 'neg' if a['net'] < 0 else ''}'>{_usd(a['net'])}</td><td>{a['pf'] if a['pf'] is not None else '—'}</td><td>{_usd(-a['commissions']) if a['commissions'] else '—'}</td>"
            f"<td>{a['contracts']}</td><td>{_usd(a['balance_end']).lstrip('+') if a['balance_end'] is not None else '—'}</td>"
            f"<td>{_usd(a['withdrawals']).lstrip('+') if a['withdrawals'] else '—'}{' <small>(' + str(a['pending']) + ' pend.)</small>' if a['pending'] else ''}</td><td>{_usd(a['paid']).lstrip('+') if a['paid'] else '—'}</td></tr>"
            for a in data["accounts"])
        rows_day = "".join(
            f"<tr><td>{e(d['weekday'])} {d['day'][8:]}/{d['day'][5:7]}</td><td>{d['n'] or '—'}</td><td>{_pct(d['win_rate']) if d['n'] else '—'}</td>"
            f"<td class='{'pos' if d['net'] > 0 else 'neg' if d['net'] < 0 else ''}'>{_usd(d['net']) if d['n'] else '—'}</td><td>{_usd(d['largest_win']) if d['n'] else '—'}</td><td>{_usd(d['largest_loss']) if d['n'] else '—'}</td></tr>"
            for d in data["days"])
        rows_sym = "".join(f"<tr><td>{e(s['key'])}</td><td>{s['n']}</td><td>{_pct(s['win_rate'])}</td><td class='{'pos' if s['net'] > 0 else 'neg' if s['net'] < 0 else ''}'>{_usd(s['net'])}</td><td>{_usd(s['avg'])}</td></tr>" for s in data["by_symbol"])
        rows_cash = "".join(
            f"<tr><td>{c['day']}</td><td>{e(c['account_id'])}</td><td>{ {'withdrawal': 'retiro', 'deposit': 'depósito', 'adjust': 'ajuste'}.get(c['kind'], c['kind']) }</td>"
            f"<td>{_usd(float(c['amount'] or 0)).lstrip('+')}</td><td>{_usd(float(c['paid'])).lstrip('+') if c['paid'] is not None else '—'}</td>"
            f"<td>{ {'pending': 'pendiente', 'completed': 'completado', 'rejected': 'rechazado'}.get(c['status'], c['status']) }</td><td>{e(c.get('note') or '')}</td></tr>"
            for c in data["cash"])
        rules = "".join(f"<li>{e(r)}</li>" for r in data["rules"])
        tone = "pos" if t["net"] > 0 else "neg" if t["net"] < 0 else ""
        return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Informe semanal {data['from']} – {data['to']} · TradePilot X</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#111;margin:28px auto;max-width:960px;padding:0 20px;line-height:1.4}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:15px;margin:22px 0 8px;border-bottom:1px solid #ddd;padding-bottom:4px}} .muted{{color:#666;font-size:12px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:14px 0}} .kpi{{border:1px solid #ddd;border-radius:8px;padding:8px 10px}}
.kpi small{{display:block;color:#666;font-size:11px;text-transform:uppercase;letter-spacing:.04em}} .kpi b{{font-size:18px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}} th,td{{text-align:right;padding:5px 6px;border-bottom:1px solid #eee;white-space:nowrap}} th:first-child,td:first-child{{text-align:left}}
th{{font-weight:600;color:#444;background:#f6f6f6}} .pos{{color:#0a7f3f;font-weight:600}} .neg{{color:#b4232c;font-weight:600}} small{{color:#777;font-weight:400}}
ol{{padding-left:20px}} .two{{display:grid;grid-template-columns:1fr 1fr;gap:18px}} .print{{position:fixed;top:12px;right:16px;padding:8px 14px;border-radius:8px;border:1px solid #888;background:#fff;cursor:pointer}}
@media print{{.print{{display:none}} body{{margin:0;max-width:none}} .two{{grid-template-columns:1fr 1fr}}}}
@media (max-width:700px){{.two{{grid-template-columns:1fr}}}}
</style></head><body>
<button class="print" onclick="window.print()">Imprimir / guardar PDF</button>
<h1>Informe semanal · {data['from']} – {data['to']}</h1>
<div class="muted">{e(data['scope'])} · generado {e(data['generated_at'][:16].replace('T', ' '))} · TradePilot X</div>
<div class="kpis">
<div class="kpi"><small>Neto de la semana</small><b class="{tone}">{_usd(t['net'])}</b></div>
<div class="kpi"><small>Operaciones</small><b>{t['n']}</b> <span class="muted">{t['wins']} ganadas · {t['losses']} perdidas</span></div>
<div class="kpi"><small>Acierto</small><b>{_pct(t['win_rate'])}</b></div>
<div class="kpi"><small>Factor de beneficio</small><b>{t['pf'] if t['pf'] is not None else '—'}</b></div>
<div class="kpi"><small>Esperanza por operación</small><b>{_usd(t['expectancy'])}</b></div>
<div class="kpi"><small>Comisiones</small><b>{_usd(-t['commissions']) if t['commissions'] else '—'}</b></div>
<div class="kpi"><small>Mayor racha de pérdidas</small><b>{t['max_loss_streak']}</b></div>
<div class="kpi"><small>Retiros pedidos / pagados</small><b>{_usd(t['withdrawals']).lstrip('+') if t['withdrawals'] else '—'} / {_usd(t['paid']).lstrip('+') if t['paid'] else '—'}</b></div>
</div>
<h2>Por cuenta</h2>
<table><thead><tr><th>Cuenta</th><th>Op.</th><th>Acierto</th><th>Neto</th><th>PF</th><th>Comis.</th><th>Contr.</th><th>Saldo cierre</th><th>Retiros</th><th>Pagado</th></tr></thead><tbody>{rows_acc or '<tr><td colspan=10>Sin cuentas con actividad.</td></tr>'}</tbody></table>
<div class="two">
<div><h2>Por día</h2><table><thead><tr><th>Día</th><th>Op.</th><th>Acierto</th><th>Neto</th><th>Mejor</th><th>Peor</th></tr></thead><tbody>{rows_day}</tbody></table></div>
<div><h2>Por instrumento</h2><table><thead><tr><th>Instr.</th><th>Op.</th><th>Acierto</th><th>Neto</th><th>Media</th></tr></thead><tbody>{rows_sym or '<tr><td colspan=5>—</td></tr>'}</tbody></table>
<h2>Mejores y peores</h2><div class="muted">Mejores</div><ol>{''.join(f'<li>{e(b)}</li>' for b in data['best']) or '<li>—</li>'}</ol><div class="muted">Peores</div><ol>{''.join(f'<li>{e(w)}</li>' for w in data['worst']) or '<li>—</li>'}</ol></div>
</div>
<h2>Retiros y pagos de la semana</h2>
<table><thead><tr><th>Día</th><th>Cuenta</th><th>Tipo</th><th>Importe</th><th>Recibido</th><th>Estado</th><th>Nota</th></tr></thead><tbody>{rows_cash or '<tr><td colspan=7>Sin movimientos de caja esta semana.</td></tr>'}</tbody></table>
{'<h2>Para la semana que viene</h2><ol>' + rules + '</ol>' if rules else ''}
<p class="muted">Neto = P&amp;L de las operaciones reconstruidas por el engine menos comisiones. Duración media ganadoras {_dur(t['avg_dur_win_s'])} · perdedoras {_dur(t['avg_dur_loss_s'])}. Los retiros son los anotados en Análisis → Pendientes y registros.</p>
</body></html>"""

    # ---- CSV ----
    def trades_csv(self, day_from: str, day_to: str, account: str | None = None, tag: str | None = None) -> str:
        ids = self.accounts_for(account, tag)
        trades = [t for t in self.store.get_trades(day_from, day_to) if ids is None or t["account_id"] in ids]
        out = io.StringIO()
        w = csv.writer(out, delimiter=";", lineterminator="\r\n")
        w.writerow(["fecha", "cuenta", "alias", "instrumento", "lado", "contratos", "entrada", "salida", "bruto_usd", "comisiones_usd", "neto_usd", "duracion_s", "abierta", "cerrada", "fills"])
        for t in trades:
            d = seconds_of(t)
            w.writerow([t["day"], t["account_id"], self._label(t["account_id"]), t["symbol"], "largo" if t["side"] == "long" else "corto", t["quantity"],
                        _num(t["entry_price"]), _num(t["exit_price"]), _num(t["pnl"]), _num(t["commissions"]), _num(net_of(t)), int(d) if d is not None else "",
                        (t.get("opened_at") or "")[:19].replace("T", " "), (t.get("closed_at") or "")[:19].replace("T", " "), t.get("fills") or ""])
        return "﻿" + out.getvalue()     # BOM: Excel en español abre el ; y los acentos sin preguntar


def _num(v) -> str:
    """Números con coma decimal (Excel es-ES)."""
    if v is None:
        return ""
    return f"{float(v):.2f}".replace(".", ",")
