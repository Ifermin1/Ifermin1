"""Robustez 2: órdenes manuales que sobreviven a un reinicio y reconciliación al arrancar."""
import json

from tradepilot.container import build_container
from tradepilot.core.config import Settings
from tradepilot.core.events import EventBus
from tradepilot.infrastructure.brokers.mock import MockBridge

ORDER = {"client_id": "c-1", "account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 2, "order_type": "MARKET", "sl_ticks": 40, "tp_ticks": 80}


def _build(db):
    cfg = Settings(ENGINE_MODE="mock", API_TOKEN="t", DB_PATH=str(db), WEB_DIST="/nonexistent", MOCK_DEMO_HISTORY=False)
    return build_container(cfg, bridge=MockBridge(EventBus(), emit_events=False))


async def test_manual_orders_and_exits_survive_a_restart(tmp_path):
    db = tmp_path / "t.db"
    c1 = _build(db)
    await c1.accounts.sync_once()
    r1 = await c1.trade.place({**ORDER, "sl_ticks": 0, "tp_ticks": 0})
    assert r1["status"] == "filled" and not r1.get("duplicate")
    rows = c1.store.manual_orders_recent()
    assert len(rows) == 1 and rows[0]["status"] == "filled" and rows[0]["client_id"] == "c-1" and json.loads(rows[0]["request"])["quantity"] == 2
    await c1.accounts.sync_once()
    pos = next(p for p in c1.accounts.find("Sim101").open_positions if p.quantity)
    await c1.trade.protect({"client_id": "p-1", "account": "Sim101", "symbol": "NQ 12-26", "sl_price": pos.avg_price - 25, "last_price": pos.avg_price})
    assert c1.trade._exits, "el stop creado por Proteger queda registrado como salida propia"
    saved_exits = json.loads(c1.store.get_kv("trade.exits"))
    assert saved_exits and saved_exits[0][0] == "Sim101"
    c1.store.close()
    # "reinicio": otro contenedor sobre la misma base de datos, mismo simulador (misma posición y órdenes vivas)
    c2 = _build(db)
    assert c2.trade._exits, "las salidas propias se recuperan de la base de datos"
    r2 = await c2.trade.place(dict(ORDER))
    assert r2.get("duplicate") is True and r2["order_id"] == r1["order_id"], "repetir el mismo client_id tras reiniciar no duplica la orden"
    assert len(c2.store.manual_orders_recent()) == 1
    assert len(c2.trade.recent_orders()) == 1 and c2.trade.recent_orders()[0]["request"]["symbol"] == "NQ 12-26"
    c2.store.close()


async def test_reconcile_reports_unconfirmed_naked_and_desync(tmp_path):
    db = tmp_path / "r.db"
    c = _build(db)
    # una orden que quedó "enviando" cuando el engine se apagó
    c.store.manual_order_upsert({"client_id": "lost-1", "account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 1, "order_type": "MARKET", "status": "sending",
                                 "order_id": "", "request": "{}", "result": "{}", "created_at": "2026-10-26T09:00:00", "updated_at": "2026-10-26T09:00:00"})
    await c.accounts.sync_once()                   # primera sincronización: reconciliación de arranque
    rep = c.reconcile.last
    assert rep is not None and rep["reason"] == "arranque"
    kinds = {f["kind"] for f in rep["findings"]}
    assert "unconfirmed_order" in kinds and c.store.manual_order_get("lost-1")["status"] == "unconfirmed"
    assert any(a.event_type == "RECONCILE" for a in c.audit.recent(20))
    # posición sin stop (orden a mercado sin brackets) -> hallazgo "naked_position" con acción proteger
    await c.trade.place({**ORDER, "client_id": "c-2", "sl_ticks": 0, "tp_ticks": 0})
    await c.accounts.sync_once()
    rep = await c.reconcile.run("manual")
    naked = [f for f in rep["findings"] if f["kind"] == "naked_position"]
    assert naked and naked[0]["account"] == "Sim101" and naked[0]["action"] == "protect" and rep["counts"].get("bad", 0) >= 1
    # la segunda sincronización no vuelve a lanzar la de arranque
    assert c.reconcile.startup_done is True
    c.store.close()


async def test_reconcile_api(client, container):
    await container.accounts.sync_once()
    r = await client.get("/api/reconcile")
    assert r.status_code == 200 and "findings" in r.json()
    r = await client.post("/api/reconcile/run")
    assert r.status_code == 200 and r.json()["reason"] == "manual"
    r = await client.get("/api/trade/orders")
    assert r.status_code == 200 and isinstance(r.json(), list)
