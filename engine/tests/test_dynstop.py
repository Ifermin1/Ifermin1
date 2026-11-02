"""Stop dinámico por cuenta: el stop copiado se acerca hasta lo que le queda a ESA cuenta (ventana − margen)."""
from tradepilot.domain.accounts import WorkingOrder
from tradepilot.domain.risk import RiskLimit


async def _setup(container, daily_loss=400.0, realized=0.0, stop=19950.0):
    b, risk = container.bridge, container.risk
    b.noise = 0
    await b.set_master("Sim101")
    b._px["NQ DEC26"] = 20000.0
    b.positions[("Sim102", "NQ DEC26")] = 2                    # larga 2 NQ a 20000
    b.pnl["Sim102"] = realized
    b.working_orders.append(("Sim102", WorkingOrder(order_id="s1", master_order_id="m1", action="SELL", symbol="NQ DEC26", quantity=2, order_type="STOPMARKET", stop_price=stop)))
    risk.upsert_limit(RiskLimit(account_id="Sim102", max_daily_loss=daily_loss))
    await container.accounts.sync_once()
    return b


async def test_stop_is_tightened_to_the_accounts_window(container):
    b = await _setup(container)                               # ventana 400 − 10 = 390 $ → 390 / (20 × 2) = 9,75 puntos
    await container.dynstop.tick()
    wo = next(o for a, o in b.working_orders if a == "Sim102")
    assert wo.stop_price == 19990.25
    ev = [a for a in container.audit.recent(10) if a.event_type == "DYNSTOP_MOVE"]
    assert ev and "19950" in ev[0].message and "19990.25" in ev[0].message
    st = container.dynstop.state()
    assert st["accounts"]["Sim102"]["room"] == 390.0 and st["accounts"]["Sim102"]["limited_by"] == "stop loss diario"


async def test_stop_inside_window_is_left_alone_and_master_is_skipped(container):
    b = await _setup(container, stop=19995.0)                 # pierde 200 $ al stop: dentro de 390
    await container.dynstop.tick()
    assert next(o for a, o in b.working_orders if a == "Sim102").stop_price == 19995.0
    assert container.dynstop.stats["moves"] == 0
    # la maestra no se toca aunque tenga ventana
    b.positions[("Sim101", "NQ DEC26")] = 2
    b.working_orders.append(("Sim101", WorkingOrder(order_id="m9", action="SELL", symbol="NQ DEC26", quantity=2, order_type="STOPMARKET", stop_price=19900.0)))
    container.risk.upsert_limit(RiskLimit(account_id="Sim101", max_daily_loss=100))
    await container.accounts.sync_once()
    await container.dynstop.tick()
    assert next(o for a, o in b.working_orders if a == "Sim101").stop_price == 19900.0


async def test_realized_loss_shrinks_window_and_no_room_leaves_it_open(container):
    b = await _setup(container, realized=-300.0)              # quedan 100 − 10 = 90 $ → 2,25 puntos → 19997.75
    await container.dynstop.tick()
    assert next(o for a, o in b.working_orders if a == "Sim102").stop_price == 19997.75
    b.pnl["Sim102"] = -395.0                                  # quedan 5 − 10 < 0: sin ventana → se deja y se avisa
    await container.accounts.sync_once()
    await container.dynstop.tick()
    assert next(o for a, o in b.working_orders if a == "Sim102").stop_price == 19997.75
    assert any(a.event_type == "DYNSTOP_NO_ROOM" for a in container.audit.recent(10))


async def test_dynstop_routes(client):
    r = await client.put("/api/dynstop", json={"margin_usd": 25, "enabled": True})
    assert r.status_code == 200 and r.json()["config"]["margin_usd"] == 25.0
    assert (await client.get("/api/dynstop")).json()["config"]["enabled"] is True
