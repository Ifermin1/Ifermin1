"""Punto 6: informe semanal imprimible y CSV de operaciones."""
from datetime import date, timedelta

from tradepilot.services.report_service import week_bounds


def _seed(container, day: str):
    st = container.store
    nxt = lambda h, m=0: f"{day}T{h:02d}:{m:02d}:00"  # noqa: E731
    rows = [
        ("Sim101", "NQ 12-26", "long", 2, 20000, 20010, 400.0, 9.0, nxt(9, 30), nxt(9, 45)),
        ("Sim101", "NQ 12-26", "short", 1, 20010, 20020, -200.0, 4.5, nxt(10, 0), nxt(10, 0)),
        ("Sim102", "CL 11-26", "long", 1, 75.0, 75.5, 500.0, 4.0, nxt(11, 0), nxt(12, 0)),
    ]
    for acc, sym, side, q, ep, xp, pnl, com, o, c in rows:
        st.save_trade({"account_id": acc, "day": day, "symbol": sym, "side": side, "quantity": q, "entry_price": ep, "exit_price": xp, "pnl": pnl, "commissions": com, "opened_at": o, "closed_at": c, "fills": 2})
    st.save_balance_day("Sim101", day, 50200.0, nxt(17))
    st.add_cash_event({"account_id": "Sim101", "day": day, "kind": "withdrawal", "amount": 1000.0, "paid": 900.0, "status": "completed", "note": "primer pago", "created_at": nxt(18)})
    st.add_cash_event({"account_id": "Sim102", "day": day, "kind": "withdrawal", "amount": 500.0, "paid": None, "status": "pending", "note": "", "created_at": nxt(18)})


def test_week_bounds():
    assert week_bounds("2026-10-21") == ("2026-10-19", "2026-10-25")       # miércoles -> lunes..domingo
    assert week_bounds("2026-10-19") == ("2026-10-19", "2026-10-25")
    assert week_bounds("2026-10-25") == ("2026-10-19", "2026-10-25")


async def test_weekly_report_data_html_and_csv(client, container):
    await container.accounts.sync_once()
    day = (date.today() - timedelta(days=date.today().weekday())).isoformat()     # lunes de esta semana
    _seed(container, day)
    await container.accounts.set_settings("Sim101", tags=["50K"])
    r = container.report.weekly(day)
    assert r["from"] <= day <= r["to"] and r["total"]["n"] == 3 and r["total"]["net"] == 682.5 and r["total"]["withdrawals"] == 1500.0 and r["total"]["paid"] == 900.0
    acc = {a["account_id"]: a for a in r["accounts"]}
    assert acc["Sim101"]["n"] == 2 and acc["Sim101"]["net"] == 186.5 and acc["Sim101"]["balance_end"] is not None and acc["Sim101"]["paid"] == 900.0
    assert acc["Sim102"]["pending"] == 1 and acc["Sim102"]["withdrawals"] == 500.0
    assert len(r["days"]) == 7 and sum(d["n"] for d in r["days"]) == 3 and r["by_symbol"][0]["key"] == "NQ"
    assert len(r["rules"]) == 3                                   # reglas del coach (hay un stop en < 60 s)
    # grupo (etiqueta) y cuenta
    assert [a["account_id"] for a in container.report.weekly(day, tag="50K")["accounts"]] == ["Sim101"]
    assert container.report.weekly(day, account="Sim102")["total"]["n"] == 1
    # HTML imprimible
    h = container.report.weekly_html(r)
    assert "<!doctype html>" in h and "Informe semanal" in h and "Sim101" in h and "+682,50 $" in h and "primer pago" in h and "window.print()" in h
    # API
    resp = await client.get(f"/api/report/weekly?day={day}")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html") and "Informe semanal" in resp.text
    resp = await client.get(f"/api/report/weekly?day={day}&format=json&tag=50K")
    assert resp.status_code == 200 and resp.json()["scope"] == "grupo 50K" and resp.json()["total"]["n"] == 2
    assert (await client.get("/api/report/weekly?day=ayer")).status_code == 400
    # CSV
    resp = await client.get(f"/api/trades.csv?day_from={r['from']}&day_to={r['to']}")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv") and "attachment" in resp.headers["content-disposition"]
    lines = resp.text.lstrip("﻿").splitlines()
    assert lines[0].startswith("fecha;cuenta;alias;instrumento;lado;contratos") and len(lines) == 4
    assert "Sim101;Sim101;NQ 12-26;largo;2;20000,00;20010,00;400,00;9,00;391,00;900;" in lines[1]
    resp = await client.get(f"/api/trades.csv?day_from={r['from']}&day_to={r['to']}&account=Sim102")
    assert len(resp.text.splitlines()) == 2
