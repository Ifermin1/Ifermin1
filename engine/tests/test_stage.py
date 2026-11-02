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
