import asyncio

from httpx import AsyncClient


async def _settle(client: AsyncClient, n: int = 3):
    for _ in range(n):
        await asyncio.sleep(0.05)


async def test_manual_market_order_on_master_is_copied_and_idempotent(client: AsyncClient, container):
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101", "multiplier": 2})
    body = {"client_id": "c-0001", "account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 1, "order_type": "MARKET", "sl_ticks": 40, "tp_ticks": 80}
    r = await client.post("/api/trade/order", json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "filled" and d["ms"] >= 0 and d["order_id"].startswith("M") and not d.get("duplicate")
    await _settle(client)
    copies = [o for o in container.bridge.sent_orders if o["account"] == "Sim102"]
    assert any(o["msg_type"] == "EXECUTION" and o["quantity"] == 2 for o in copies)            # la maestra se replica ×2
    assert any(o["msg_type"] == "ORDER_PENDING" and o["order_type"] == "STOPMARKET" for o in copies)   # y su stop automático
    assert container.bridge.positions[("Sim101", "NQ 12-26")] == 1
    # reenviar el mismo client_id (respuesta perdida): mismo resultado, ninguna orden nueva
    r2 = await client.post("/api/trade/order", json=body)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True and r2.json()["order_id"] == d["order_id"]
    assert container.bridge.positions[("Sim101", "NQ 12-26")] == 1
    types = [a.event_type for a in container.audit.recent(20)]
    assert "MANUAL_ORDER" in types
    st = (await client.get("/api/trade/status")).json()
    assert st["ready"] is True and st["stats"]["orders"] == 1 and st["last_order_ms"] is not None and st["max_qty"] == 20


async def test_manual_order_validations(client: AsyncClient, container):
    bad = lambda **kw: {"client_id": "c-x", "account": "Sim101", "symbol": "ES 12-26", "action": "BUY", "quantity": 1, "order_type": "MARKET", **kw}  # noqa: E731
    assert (await client.post("/api/trade/order", json=bad(quantity=50))).status_code == 422
    assert (await client.post("/api/trade/order", json=bad(order_type="LIMIT"))).status_code == 422          # sin precio límite
    assert (await client.post("/api/trade/order", json=bad(account="Nope"))).status_code == 422
    assert (await client.post("/api/trade/order", json=bad(action="HOLD"))).status_code == 422
    assert (await client.post("/api/trade/order", json=bad(client_id="x"))).status_code == 422                # client_id corto
    await client.post("/api/risk/kill-switch", json={"active": True, "reason": "test"})
    r = await client.post("/api/trade/order", json=bad(client_id="c-kill"))
    assert r.status_code == 422 and "kill switch" in r.text
    st = (await client.get("/api/trade/status")).json()
    assert st["ready"] is False and "kill switch" in st["reason"]
    await client.post("/api/risk/kill-switch", json={"active": False})
    assert [a.event_type for a in container.audit.recent(3)][:1] != ["MANUAL_ORDER"]
    assert any(a.event_type == "MANUAL_REJECTED" for a in container.audit.recent(20))


async def test_limit_order_cancel_modify_and_close(client: AsyncClient, container):
    r = await client.post("/api/trade/order", json={"client_id": "c-lim-1", "account": "Sim102", "symbol": "ES 12-26", "action": "SELL", "quantity": 2, "order_type": "LIMIT", "limit_price": 5700})
    assert r.status_code == 200 and r.json()["status"] == "working"
    oid = r.json()["order_id"]
    await container.accounts.sync_once()
    acc = next(a for a in container.accounts.all() if a.account_id == "Sim102")
    assert [(o.order_id, o.limit_price, o.quantity) for o in acc.working_orders] == [(oid, 5700, 2)]
    r = await client.post("/api/trade/modify", json={"account": "Sim102", "order_id": oid, "limit_price": 5690})
    assert r.status_code == 200
    await container.accounts.sync_once()
    acc = next(a for a in container.accounts.all() if a.account_id == "Sim102")
    assert acc.working_orders[0].limit_price == 5690 and acc.working_orders[0].quantity == 2
    r = await client.post("/api/trade/cancel", json={"account": "Sim102", "order_id": oid})
    assert r.status_code == 200
    assert (await client.post("/api/trade/cancel", json={"account": "Sim102", "order_id": oid})).status_code == 502   # ya no existe
    await container.accounts.sync_once()
    assert next(a for a in container.accounts.all() if a.account_id == "Sim102").working_orders == []
    # cerrar solo una posición
    container.bridge.positions[("Sim102", "ES 12-26")] = -3
    await container.accounts.sync_once()
    r = await client.post("/api/trade/close", json={"client_id": "c-close-1", "account": "Sim102", "symbol": "ES 12-26"})
    assert r.status_code == 200 and r.json()["action"] == "BUY" and r.json()["quantity"] == 3
    assert container.bridge.positions[("Sim102", "ES 12-26")] == 0
    await container.accounts.sync_once()
    assert (await client.post("/api/trade/close", json={"client_id": "c-close-2", "account": "Sim102", "symbol": "ES 12-26"})).status_code == 422
    types = [a.event_type for a in container.audit.recent(30)]
    assert "MANUAL_CANCEL" in types and "MANUAL_MODIFY" in types


async def test_manual_status_from_addon_is_audited(container):
    await container.replication.process_master_event({"msg_type": "MANUAL_STATUS", "account": "Sim102", "manual_id": "c-9", "action": "BUY", "quantity": 1,
                                                       "symbol": "NQ 12-26", "order_type": "MARKET", "state": "Rejected", "error": "OrderRejected", "native_error": "margen"})
    await container.replication.process_master_event({"msg_type": "MANUAL_STATUS", "account": "Sim102", "manual_id": "c-10", "action": "BUY", "quantity": 1,
                                                       "symbol": "NQ 12-26", "order_type": "MARKET", "state": "Filled", "avg_price": 20010.25})
    types = [a.event_type for a in container.audit.recent(5)]
    assert "MANUAL_REJECTED" in types and "MANUAL_FILL" in types


def test_zmq_parse_manual_helpers():
    from tradepilot.infrastructure.brokers.ninja_zmq import NinjaZmqBridge
    assert NinjaZmqBridge.parse_orders("Sim101|abc|c-1|BUY|NQ 12-26|1|0|LIMIT|20000|0|Working")[0][1].order_id == "abc"


async def test_protect_breakeven_quick_exits_and_orphan_sweep(client: AsyncClient, container):
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101", "multiplier": 1})
    r = await client.post("/api/trade/order", json={"client_id": "c-prot-1", "account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 2, "order_type": "MARKET"})
    assert r.status_code == 200
    await container.accounts.sync_once()
    pv = (await client.get("/api/trade/protect?account=Sim101&symbol=NQ%2012-26")).json()
    assert pv["quantity"] == 2 and pv["stops"] == [] and pv["targets"] == [] and pv["point_value"] == 20 and pv["tick"] == 0.25
    avg = pv["avg_price"]
    # stop que se dispararía al instante: rechazado salvo force
    r = await client.post("/api/trade/protect", json={"client_id": "prot-1", "account": "Sim101", "symbol": "NQ 12-26", "sl_price": avg + 10, "last_price": avg + 2})
    assert r.status_code == 422 and "al instante" in r.text
    # stop y objetivo rápidos: se crean los dos enlazados en OCO
    r = await client.post("/api/trade/protect", json={"client_id": "prot-2", "account": "Sim101", "symbol": "NQ 12-26", "sl_price": avg - 10, "tp_price": avg + 20, "last_price": avg + 2})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["sl"]["action"] == "created" and d["tp"]["action"] == "created" and d["sl"]["price"] == avg - 10 and d["tp"]["price"] == avg + 20
    assert set(container.bridge.oco_groups.values()) == {"TPM-OCO-prot-2"}
    await _settle(client)
    copies = [o for o in container.bridge.sent_orders if o["account"] == "Sim102" and o["msg_type"] == "ORDER_PENDING"]
    assert {o["order_type"] for o in copies} == {"STOPMARKET", "LIMIT"}                 # la seguidora copia las dos salidas
    await container.accounts.sync_once()
    # breakeven + 1 tick: modifica el stop existente (y la seguidora recibe ORDER_MODIFIED)
    r = await client.post("/api/trade/protect", json={"client_id": "prot-3", "account": "Sim101", "symbol": "NQ 12-26", "breakeven": True, "be_offset_ticks": 1, "last_price": avg + 5})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["sl"]["action"] == "modified" and d["sl"]["price"] == avg + 0.25 and d["tp"] is None
    await _settle(client)
    assert any(o["account"] == "Sim102" and o["msg_type"] == "ORDER_MODIFIED" and o["stop_price"] == avg + 0.25 for o in container.bridge.sent_orders)
    await container.accounts.sync_once()
    pv = (await client.get("/api/trade/protect?account=Sim101&symbol=NQ%2012-26")).json()
    assert pv["stops"][0]["stop_price"] == avg + 0.25 and pv["targets"][0]["limit_price"] == avg + 20
    # la posición se cierra por fuera: las salidas creadas por Proteger se cancelan solas
    container.bridge.positions[("Sim101", "NQ 12-26")] = 0
    await container.accounts.sync_once()
    await container.trade.sweep_orphans()
    await container.accounts.sync_once()
    assert next(a for a in container.accounts.all() if a.account_id == "Sim101").working_orders == []
    types = [a.event_type for a in container.audit.recent(40)]
    assert "MANUAL_PROTECT" in types and types.count("MANUAL_CANCEL") >= 2
    assert container.trade.stats["orphans"] == 2
