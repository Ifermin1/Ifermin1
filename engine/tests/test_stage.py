"""Etapas (modo prueba): recorte por fecha y por cuentas nuevas en el almacén, ver todo, y borrado con copia previa."""
from datetime import date, timedelta

from tradepilot.core.scope import stage_bypass

TODAY = date.today().isoformat()
OLD = (date.today() - timedelta(days=10)).isoformat()
FROM = (date.today() - timedelta(days=20)).isoformat()


def _trade(acc, day, pnl=10.0):
    return {"account_id": acc, "day": day, "symbol": "NQ DEC26", "side": "long", "quantity": 1, "entry_price": 1.0, "exit_price": 2.0,
            "pnl": pnl, "commissions": 0.0, "opened_at": f"{day}T10:00:00", "closed_at": f"{day}T10:05:00", "fills": 2}


def _seed(store):
    store.save_trade(_trade("Sim101", OLD))
    store.save_trade(_trade("Sim101", TODAY))
    store.save_trade(_trade("Sim102", TODAY))
    store.save_daily_pnl("Sim101", OLD, 10.0, "x")
    store.save_daily_pnl("Sim101", TODAY, 10.0, "x")


def test_stage_by_date_hides_old_history_and_scope_all_shows_it(container):
    st, store = container.stage, container.store
    _seed(store)
    assert len(store.get_trades("0000-00-00", TODAY)) == 3
    st.start("Prueba", "date", TODAY)
    assert len(store.get_trades("0000-00-00", TODAY)) == 2
    assert len(store.get_daily_pnl("0000-00-00", TODAY)) == 1
    with stage_bypass():
        assert len(store.get_trades("0000-00-00", TODAY)) == 3
    s = st.state()
    assert s["active"] and s["counts"] == {"trades_stage": 2, "trades_total": 3}
    st.stop()
    assert len(store.get_trades("0000-00-00", TODAY)) == 3


async def test_stage_new_accounts_only_counts_accounts_seen_after_start(container):
    st, store = container.stage, container.store
    await container.accounts.sync_once()      # Sim101..103 pasan a ser cuentas conocidas
    _seed(store)
    st.start("Cuentas nuevas", "new_accounts", TODAY)
    # Sim101 y Sim102 ya existían: fuera; una cuenta nueva entra sola
    store.save_trade(_trade("Sim999", TODAY))
    ids = {t["account_id"] for t in store.get_trades("0000-00-00", TODAY)}
    assert ids == {"Sim999"}
    st.update(include=["Sim102"])
    assert {t["account_id"] for t in store.get_trades("0000-00-00", TODAY)} == {"Sim999", "Sim102"}
    st.update(exclude=["Sim999"], include=["Sim102"])
    assert {t["account_id"] for t in store.get_trades("0000-00-00", TODAY)} == {"Sim102"}
    accs = {a["account_id"]: a for a in st.state()["accounts"]}
    assert accs["Sim101"]["is_new"] is False and accs["Sim101"]["in_scope"] is False
    assert accs["Sim102"]["in_scope"] is True


def test_reset_history_purges_before_day_and_needs_confirmation(container):
    st, store = container.stage, container.store
    _seed(store)
    try:
        st.reset_history(TODAY, "no")
        assert False, "debe exigir BORRAR"
    except ValueError:
        pass
    r = st.reset_history(TODAY, "BORRAR")
    assert r["deleted"]["trades"] == 1 and r["deleted"]["daily_stats"] == 1
    assert len(store.get_trades("0000-00-00", TODAY)) == 2
    assert any(a.event_type == "HISTORY_PURGED" for a in container.audit.recent(3))


async def test_stage_routes(client, container):
    _seed(container.store)
    r = await client.put("/api/stage", json={"action": "start", "name": "Prueba", "mode": "date", "start_date": TODAY})
    assert r.status_code == 200 and r.json()["active"]
    h = await client.get("/api/health")
    assert h.json()["stage"]["name"] == "Prueba"
    m = await client.get(f"/api/stats/analysis?day_from={FROM}&day_to={TODAY}")
    assert len(m.json()["trades"]) == 2
    m = await client.get(f"/api/stats/analysis?day_from={FROM}&day_to={TODAY}&scope=all")
    assert len(m.json()["trades"]) == 3
    r = await client.post("/api/stage/reset", json={"before": TODAY, "confirm": "x"})
    assert r.status_code == 422
    r = await client.put("/api/stage", json={"action": "stop"})
    assert r.json()["active"] is False


async def _goal_setup(container, goal_master):
    b, st, risk, rep = container.bridge, container.stage, container.risk, container.replication
    b.noise = 0
    await container.accounts.sync_once()
    await b.set_master("Sim101")
    rep.add_rule("Sim101", "Sim102")
    rep.add_rule("Sim101", "Sim103")
    st.start("Prueba", "date", TODAY)
    s = st.set_goal(1500, goal_master)
    return b, st, risk, rep, s


async def test_goal_applies_to_stage_accounts_and_follower_closes_alone(container):
    b, st, risk, rep, s = await _goal_setup(container, "flatten_all")
    for acc in ("Sim101", "Sim102", "Sim103"):
        lim = risk.limits[acc]
        assert lim.profit_goal == 1500 and lim.start_balance == b.accounts[acc]
    assert all(a["goal"]["pct"] == 0 for a in s["accounts"])
    # la seguidora Sim102 gana 1600: se cierra y pausa sola; la maestra y el kill switch no cambian
    b.accounts["Sim102"] += 1600
    await container.accounts.sync_once()
    await risk.check()
    assert risk.limits["Sim102"].trading_halted and risk.limits["Sim102"].halted_reason == "profit_goal"
    assert not risk.limits["Sim101"].trading_halted and not risk.state().kill_switch
    acc = {a["account_id"]: a for a in st.state()["accounts"]}
    assert acc["Sim102"]["goal"]["reached"] is True and acc["Sim102"]["goal"]["pct"] >= 100


async def test_goal_master_first_flatten_all_stops_copy(container):
    b, st, risk, rep, _ = await _goal_setup(container, "flatten_all")
    b.accounts["Sim101"] += 1500
    await container.accounts.sync_once()
    await risk.check()
    assert risk.limits["Sim101"].halted_reason == "profit_goal"
    assert risk.state().kill_switch and "objetivo de prueba" in (risk.state().kill_switch_reason or "")
    assert any(a.event_type == "STAGE_GOAL_MASTER" for a in container.audit.recent(10))


async def test_goal_master_first_rotate_promotes_furthest_follower(container):
    b, st, risk, rep, _ = await _goal_setup(container, "rotate")
    b.accounts["Sim103"] += 900      # Sim103 va por +900; Sim102 por 0 -> Sim102 es la nueva maestra
    b.accounts["Sim101"] += 1500
    await container.accounts.sync_once()
    await risk.check()
    assert b.health.master_account == "Sim102"
    masters = {(r.master_account, r.follower_account) for r in rep.rules if r.enabled}
    assert masters == {("Sim102", "Sim103")}
    assert not risk.state().kill_switch
    ev = [a for a in container.audit.recent(10) if a.event_type == "STAGE_GOAL_MASTER"]
    assert ev and "NUEVA MAESTRA: Sim102" in ev[0].message


async def test_goal_zero_removes_goal_and_resumes(container):
    b, st, risk, rep, _ = await _goal_setup(container, "master_only")
    b.accounts["Sim102"] += 1600
    await container.accounts.sync_once()
    await risk.check()
    assert risk.limits["Sim102"].trading_halted
    st.set_goal(0)
    assert risk.limits["Sim102"].profit_goal == 0 and not risk.limits["Sim102"].trading_halted
