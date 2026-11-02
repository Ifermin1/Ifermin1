"""Gestor preventivo: dueño por cuenta, semáforo (drawdown, MAE, consistencia, contratos, inactividad), cartera y choques."""
from datetime import date, timedelta

from tradepilot.domain.risk import RiskLimit


async def _ready(container):
    b = container.bridge
    b.noise = 0
    await container.accounts.sync_once()
    return b


async def test_owner_is_saved_and_filters_portfolio(client, container):
    await _ready(container)
    r = await client.patch("/api/accounts/Sim102", json={"owner": "Ibrahim"})
    assert r.status_code == 200 and r.json()["owner"] == "Ibrahim"
    pf = (await client.get("/api/guardian?owner=Ibrahim")).json()
    assert [a["account_id"] for a in pf["accounts"]] == ["Sim102"] and pf["owners"] == ["Ibrahim"] and pf["unassigned"] == 2
    assert pf["verdict"] in ("OPERATIVA HABILITADA SEGÚN EL PLAN", "OPERATIVA CON RIESGO ELEVADO", "RECOMENDACIÓN DE NO OPERAR")


async def test_mae_warn_and_breach_with_optional_flatten(container):
    b = await _ready(container)
    g, risk = container.guardian, container.risk
    # cuenta financiada con 2.000 $ de beneficio de referencia: límite MAE 30 % = 600 $, preventivo 20 % = 400 $
    risk.upsert_limit(RiskLimit(account_id="Sim101", start_balance=b.accounts["Sim101"] - 2000, max_trailing_drawdown=2500))
    b.unrealized["Sim101"] = -450.0
    await container.accounts.sync_once()
    r = g.evaluate("Sim101")
    assert r["mae"]["limit"] == 600.0 and r["mae"]["pct"] == 75.0
    assert [a["code"] for a in r["alerts"]] == ["MAE_WARN"] and r["status"] == "amber"
    b.unrealized["Sim101"] = -650.0
    await container.accounts.sync_once()
    r = g.evaluate("Sim101")
    assert any(a["code"] == "MAE_BREACH" and a["level"] == "red" for a in r["alerts"]) and r["status"] == "red"
    await g.tick()
    assert any(a.event_type == "MAE_BREACH" for a in container.audit.recent(10))
    g.set_config(auto_flatten_mae=True)
    g._noted.clear()
    await g.tick()
    assert any(a.event_type == "FLATTEN" for a in container.audit.recent(10))


async def test_liquidation_near_and_inactivity(container):
    b = await _ready(container)
    g, risk = container.guardian, container.risk
    risk.upsert_limit(RiskLimit(account_id="Sim103", start_balance=b.accounts["Sim103"], max_trailing_drawdown=200, drawdown_buffer=250))
    await container.accounts.sync_once()
    r = g.evaluate("Sim103")
    assert any(a["code"] == "LIQUIDATION_NEAR" for a in r["alerts"]) and r["status"] == "red"
    old = (date.today() - timedelta(days=31)).isoformat()
    container.store.save_trade({"account_id": "Sim103", "day": old, "symbol": "NQ DEC26", "side": "long", "quantity": 1, "entry_price": 1, "exit_price": 2, "pnl": 10, "commissions": 0, "opened_at": f"{old}T10:00:00", "closed_at": f"{old}T10:05:00", "fills": 2})
    r = g.evaluate("Sim103")
    assert r["inactive_days"] == 31 and any(a["code"] == "INACTIVITY" and a["level"] == "red" for a in r["alerts"])


async def test_portfolio_shocks_use_open_positions(container):
    b = await _ready(container)
    g, risk = container.guardian, container.risk
    risk.upsert_limit(RiskLimit(account_id="Sim101", start_balance=b.accounts["Sim101"], max_trailing_drawdown=2500))
    b.positions[("Sim101", "NQ DEC26")] = 2         # larga 2 NQ: −10 puntos = −400 $
    await container.accounts.sync_once()
    pf = g.portfolio()
    s10 = next(s for s in pf["shocks"] if s["points"] == 10)
    assert s10["pnl"] == -400.0 and s10["liquidated"] == []
    assert pf["totals"]["nq_equiv"] == 2.0 and pf["totals"]["open_contracts"] == 2


async def test_apex_legacy_rules_cap_first_payouts(container):
    await _ready(container)
    pr = container.proprules
    rule = pr.rule_for("Sim101", "apex")
    assert rule["min_days"] == 8 and rule["winning_days"] == 5 and rule["min_day_profit"] == 50 and rule["payout_max"] == 2000 and rule["payout_max_until"] == 5


async def test_check_entry_flags_risk_reward_over_5_to_1(container):
    await _ready(container)
    r = await container.coach.check_entry({"account": "Sim101", "symbol": "NQ DEC26", "action": "BUY", "quantity": 1, "order_type": "MARKET", "sl_ticks": 60, "tp_ticks": 10})
    assert any("5:1" in x["text"] and x["level"] == "red" for x in r["reasons"])
