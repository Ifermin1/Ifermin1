"""IA 6: reglas de prop firm (payout, consistencia, días, colchón) y aviso antes de romperlas."""
from datetime import datetime, timedelta

from tradepilot.domain.risk import RiskLimit


async def _setup(container, firm="apex", plan="pa", start=50000.0, dd=2500.0):
    await container.accounts.sync_once()
    await container.accounts.set_settings("Sim101", firm=firm, plan=plan, plan_size=start)
    container.risk.upsert_limit(RiskLimit(account_id="Sim101", start_balance=start, max_trailing_drawdown=dd))
    return container.accounts.find("Sim101")


def _days(container, acc, pnls):
    d0 = datetime.now() - timedelta(days=len(pnls) + 1)
    for i, p in enumerate(pnls):
        day = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
        container.store.save_daily_pnl(acc, day, p, day + "T17:00:00")


async def test_apex_pa_requirements_and_missing(container):
    snap = await _setup(container)
    snap.balance = 51500.0; snap.unrealized_pnl = 0.0; snap.net_pnl = 0.0
    _days(container, "Sim101", [400, 300, 800])          # 3 días, mejor día 800 de 1500
    r = container.proprules.evaluate("Sim101")
    assert r["firm"] == "apex" and not r["is_eval"] and r["profit"] == 1500.0 and r["traded_days"] == 3
    reqs = {x["key"]: x for x in r["requirements"]}
    assert reqs["safety_net"]["status"] == "pending" and reqs["safety_net"]["missing"] == 1100.0          # 2500 + 100 − 1500
    assert reqs["days"]["status"] == "pending" and reqs["days"]["missing"] == 5
    assert reqs["consistency"]["status"] == "pending" and r["best_day_share"] == 53.3 and reqs["consistency"]["missing"] > 0
    assert r["eligible"] is False and r["max_today"] is not None
    # tras más días y más ganancia, elegible
    snap.balance = 53200.0
    _days(container, "Sim101", [400, 300, 800, 500, 400, 300, 200, 300])
    r = container.proprules.evaluate("Sim101")
    assert r["profit"] == 3200.0 and r["traded_days"] >= 8
    assert all(x["status"] == "ok" for x in r["requirements"]), r["requirements"]
    assert r["eligible"] is True and r["available"] == 3200.0
    # ajuste por cuenta
    rule = container.proprules.set_rule("Sim101", {"min_days": 10})
    assert rule["min_days"] == 10 and container.proprules.evaluate("Sim101")["eligible"] is False


async def test_eval_account_tracks_goal_only(container):
    snap = await _setup(container, plan="eval")
    container.risk.upsert_limit(RiskLimit(account_id="Sim101", start_balance=50000.0, max_trailing_drawdown=2500.0, profit_goal=3000.0))
    snap.balance = 51000.0; snap.unrealized_pnl = 0.0
    r = container.proprules.evaluate("Sim101")
    assert r["is_eval"] and [x["key"] for x in r["requirements"]] == ["goal"] and r["requirements"][0]["missing"] == 2000.0 and r["eligible"] is False


async def test_consistency_warning_once_per_day(container):
    snap = await _setup(container)
    _days(container, "Sim101", [500, 500])                # antes de hoy: +1000 → hoy máx 0.3·1000/0.7 ≈ 428 $
    snap.net_pnl = 400.0; snap.balance = 51400.0; snap.unrealized_pnl = 0.0
    r = container.proprules.evaluate("Sim101")
    assert r["max_today"] and abs(r["max_today"] - 428.57) < 0.1 and r["today"] == 400.0
    assert container.proprules.check() == ["Sim101"]
    assert container.proprules.check() == []               # no repite hoy
    assert any(a.event_type == "PROP_RULE_WARN" and "quedan" in a.message for a in container.audit.recent(10))


async def test_proprules_api(client, container):
    await _setup(container)
    r = await client.get("/api/proprules")
    assert r.status_code == 200 and r.json()["accounts"][0]["account_id"] == "Sim101"
    r = await client.put("/api/proprules", json={"account_id": "Sim101", "consistency": 0.5})
    assert r.status_code == 200 and r.json()["rule"]["consistency"] == 0.5
    assert (await client.put("/api/proprules", json={"account_id": "Nope"})).status_code == 404
