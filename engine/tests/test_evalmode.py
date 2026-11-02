"""Modo evaluación: reglas de la prueba por cuenta y gestor automático (consistencia, días, objetivo, superada)."""
from datetime import date, datetime, timedelta

from tradepilot.domain.risk import RiskLimit

TODAY = date.today().isoformat()


def _day(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _trade(acc, day, pnl):
    return {"account_id": acc, "day": day, "symbol": "NQ DEC26", "side": "long", "quantity": 1, "entry_price": 1.0, "exit_price": 2.0,
            "pnl": pnl, "commissions": 0.0, "opened_at": f"{day}T10:00:00", "closed_at": f"{day}T10:05:00", "fills": 2}


async def _setup(container, auto=True, consistency=0.5, min_days=3, start_extra=0.0):
    b, pr, risk = container.bridge, container.proprules, container.risk
    b.noise = 0
    await container.accounts.sync_once()
    await container.accounts.set_settings("Sim102", None, None, None, "topstep", "combine", 50000, None)
    risk.upsert_limit(RiskLimit(account_id="Sim102", profit_goal=1000, start_balance=b.accounts["Sim102"] + start_extra))
    pr.set_eval_rule("Sim102", {"auto": auto, "consistency": consistency, "min_days": min_days, "maint_contracts": 1})
    return b, pr, risk


async def test_eval_rules_and_status_in_progress(container):
    b, pr, risk = await _setup(container)
    for i, pnl in ((1, 100.0), (2, 100.0)):
        container.store.save_trade(_trade("Sim102", _day(i), pnl))
        container.store.save_daily_pnl("Sim102", _day(i), pnl, "x")
    r = pr.evaluate("Sim102")
    assert r["is_eval"] and r["status"] == "in_progress" and r["eval_rule"]["auto"] is True
    keys = {x["key"]: x for x in r["requirements"]}
    assert keys["days"]["detail"] == "2 de 3" and keys["consistency"]["status"] == "ok" and keys["goal"]["status"] == "pending"
    assert r["max_today"] == 200.0        # 50 %: hoy ≤ 0.5·200/(1−0.5)


async def test_consistency_stop_pauses_for_the_day_and_resumes_tomorrow(container):
    b, pr, risk = await _setup(container)
    container.store.save_daily_pnl("Sim102", _day(1), 200.0, "x")
    container.store.save_trade(_trade("Sim102", _day(1), 200.0))
    b.pnl["Sim102"] = 250.0                 # hoy +250 ≥ máximo 200
    await container.accounts.sync_once()
    await pr.guard()
    lim = risk.limits["Sim102"]
    assert lim.trading_halted and lim.halted_reason == "consistency"
    assert any(a.event_type == "EVAL_CONSISTENCY_STOP" for a in container.audit.recent(5))
    assert risk.allows("Sim102", 1)[0] is False
    # al día siguiente (P&L de hoy a cero) se levanta sola
    risk.limits["Sim102"] = lim.model_copy(update={"halted_at": datetime.now() - timedelta(days=1)})
    b.pnl["Sim102"] = 0.0
    await container.accounts.sync_once()
    await pr.guard()
    assert not risk.limits["Sim102"].trading_halted
    assert any(a.event_type == "EVAL_RESUMED" for a in container.audit.recent(20))


async def test_target_reached_with_days_pending_keeps_trading_small(container):
    b, pr, risk = await _setup(container, consistency=0.0, min_days=3)
    risk.upsert_limit(risk.limits["Sim102"].model_copy(update={"max_position_size": 5}))
    container.store.save_daily_pnl("Sim102", _day(1), 400.0, "x")
    container.store.save_trade(_trade("Sim102", _day(1), 400.0))
    b.accounts["Sim102"] += 1200          # objetivo (1000) superado, solo 1 día operado
    await container.accounts.sync_once()
    await risk.check()                    # el riesgo NO cierra: la compuerta dice que faltan días
    assert not risk.limits["Sim102"].trading_halted
    await pr.guard()
    lim = risk.limits["Sim102"]
    assert not lim.trading_halted and lim.max_position_size == 1
    assert any(a.event_type == "EVAL_TARGET_WAIT" for a in container.audit.recent(5))


async def test_all_rules_met_closes_as_passed(container):
    b, pr, risk = await _setup(container, consistency=0.5, min_days=2)
    for i, pnl in ((1, 300.0), (2, 300.0)):
        container.store.save_daily_pnl("Sim102", _day(i), pnl, "x")
        container.store.save_trade(_trade("Sim102", _day(i), pnl))
    b.accounts["Sim102"] += 1100
    await container.accounts.sync_once()
    await pr.guard()
    lim = risk.limits["Sim102"]
    assert lim.trading_halted and lim.halted_reason == "profit_goal"
    ev = [a for a in container.audit.recent(20) if a.event_type == "EVAL_PASSED"]
    assert ev and "SUPERADA" in ev[0].message


async def test_eval_routes(client, container):
    await container.accounts.sync_once()
    r = await client.put("/api/eval/Sim102", json={"auto": True, "min_days": 4, "consistency": 0.4})
    assert r.status_code == 200 and r.json()["eval_rule"]["min_days"] == 4
    r = await client.get("/api/eval/Sim102")
    assert r.json()["eval_rule"]["auto"] is True
    assert (await client.get("/api/eval/NoExiste")).status_code == 404


async def test_daily_profit_cut_lifts_itself_next_day(container):
    """La pausa por ganancia máxima del día (o stop loss diario) caduca sola al día siguiente."""
    b, risk = container.bridge, container.risk
    b.noise = 0
    await container.accounts.sync_once()
    risk.upsert_limit(RiskLimit(account_id="Sim103", max_daily_profit=100))
    b.pnl["Sim103"] = 150.0
    await container.accounts.sync_once()
    await risk.check()
    lim = risk.limits["Sim103"]
    assert lim.trading_halted and lim.halted_reason == "daily_profit"
    # mismo día: sigue pausada aunque el P&L baje
    b.pnl["Sim103"] = 0.0
    await container.accounts.sync_once()
    await risk.check()
    assert risk.limits["Sim103"].trading_halted
    # día siguiente
    risk.limits["Sim103"] = risk.limits["Sim103"].model_copy(update={"halted_at": datetime.now() - timedelta(days=1)})
    await risk.check()
    assert not risk.limits["Sim103"].trading_halted
    assert any(a.event_type == "DAY_LIMIT_RESET" for a in container.audit.recent(10))
