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


async def test_close_cancels_exits_and_addon_brackets_are_swept(client: AsyncClient, container):
    r = await client.post("/api/trade/order", json={"client_id": "c-brk-1", "account": "Sim102", "symbol": "NQ 12-26", "action": "SELL", "quantity": 2, "order_type": "MARKET", "sl_ticks": 20, "tp_ticks": 40})
    assert r.status_code == 200
    await container.accounts.sync_once()
    acc = next(a for a in container.accounts.all() if a.account_id == "Sim102")
    assert len(acc.working_orders) == 2
    # cerrar la posición desde la consola cancela el stop y el objetivo que quedaban vivos
    r = await client.post("/api/trade/close", json={"client_id": "c-brk-close", "account": "Sim102", "symbol": "NQ 12-26"})
    assert r.status_code == 200 and r.json()["cancelled_exits"] == 2
    await container.accounts.sync_once()
    assert next(a for a in container.accounts.all() if a.account_id == "Sim102").working_orders == []
    assert any(a.event_type == "MANUAL_CANCEL" and "se cancelan sus salidas" in a.message for a in container.audit.recent(10))
    # un stop automático del addon (MANUAL_STATUS con id -SL) queda vigilado y se cancela cuando la posición desaparece
    r = await client.post("/api/trade/order", json={"client_id": "c-brk-2", "account": "Sim103", "symbol": "ES 12-26", "action": "BUY", "quantity": 1, "order_type": "MARKET"})
    assert r.status_code == 200
    from tradepilot.domain.accounts import WorkingOrder
    container.bridge.working_orders.append(("Sim103", WorkingOrder(order_id="brk-sl-1", action="SELL", symbol="ES 12-26", quantity=1, order_type="STOPMARKET", stop_price=5590)))
    await container.replication.process_master_event({"msg_type": "MANUAL_STATUS", "account": "Sim103", "manual_id": "c-brk-2-SL", "order_id": "brk-sl-1", "action": "SELL",
                                                       "quantity": 1, "symbol": "ES 12-26", "order_type": "STOPMARKET", "state": "Working"})
    await container.accounts.sync_once()
    await container.trade.sweep_orphans()
    assert any(o.order_id == "brk-sl-1" for _, o in container.bridge.working_orders)          # con posición: se queda
    container.bridge.positions[("Sim103", "ES 12-26")] = 0                                      # cerrada por fuera (a mano en NinjaTrader)
    await container.accounts.sync_once()
    await container.trade.sweep_orphans()
    assert not any(o.order_id == "brk-sl-1" for _, o in container.bridge.working_orders)


async def test_auto_risk_profile_contract_cap_and_day_closed(client: AsyncClient, container):
    b = container.bridge
    # aparece una cuenta APEX de 50K: el engine le aplica el perfil solo (evaluación 50K: 2.500 de drawdown, 5 contratos como máximo)
    b.accounts["APEX112924000009999"] = 50_000.0
    await container.accounts.sync_once()
    lim = container.risk.limits.get("APEX112924000009999")
    assert lim is not None and lim.max_trailing_drawdown == 2500 and lim.drawdown_mode == "intraday" and lim.max_position_size == 5 and lim.profit_goal == 3000 and lim.start_balance == 50000
    acc = next(a for a in container.accounts.all() if a.account_id == "APEX112924000009999")
    assert acc.firm == "apex" and acc.plan == "eval" and acc.plan_size == 50000
    assert any(a.event_type == "AUTO_RISK_APPLIED" for a in container.audit.recent(10))
    # una Sim no se toca sola; el botón de Riesgo (todas las que falten) sí la perfila con la firma por defecto
    assert container.risk.limits.get("Sim103") is None
    st = (await client.get("/api/risk/auto")).json()
    assert st["enabled"] and "Sim103" in st["pending"]
    r = await client.post("/api/risk/auto", json={})
    assert r.status_code == 200 and any(a["account_id"] == "Sim103" and a["size"] == 100000 for a in r.json()["applied"])
    # tope de contratos también en Operar: 6 contratos en una cuenta con máximo 5 se rechaza
    r = await client.post("/api/trade/order", json={"client_id": "cap-1", "account": "APEX112924000009999", "symbol": "NQ 12-26", "action": "BUY", "quantity": 6, "order_type": "MARKET"})
    assert r.status_code == 422 and "límite de riesgo" in r.text
    r = await client.post("/api/trade/order", json={"client_id": "cap-2", "account": "APEX112924000009999", "symbol": "NQ 12-26", "action": "BUY", "quantity": 2, "order_type": "MARKET"})
    assert r.status_code == 200
    # día cerrado: pérdida conjunta de las cuentas activas ≥ límite → kill switch + cierre de todo
    r = await client.put("/api/risk/day-loss", json={"limit": 1000})
    assert r.status_code == 200 and r.json()["day_loss_limit"] == 1000 and r.json()["day_closed"] is False
    b.pnl["Sim101"], b.pnl["Sim102"] = -700.0, -400.0
    await container.accounts.sync_once()
    await container.risk.check()
    st = container.risk.state()
    assert st.day_closed and st.kill_switch and "día cerrado" in (st.kill_switch_reason or "") and st.day_total <= -1000
    assert b.positions.get(("APEX112924000009999", "NQ 12-26"), 0) == 0            # cerrada con el resto
    assert any(a.event_type == "DAY_CLOSED" for a in container.audit.recent(20))
    # reanudar a mano levanta el bloqueo y no vuelve a saltar hoy
    await client.post("/api/risk/kill-switch", json={"active": False})
    await container.risk.check()
    st = container.risk.state()
    assert not st.kill_switch and not st.day_closed
    assert any(a.event_type == "DAY_REOPENED" for a in container.audit.recent(10))


async def test_tags_presets_and_advice(client: AsyncClient, container):
    # etiquetas por cuenta y en lote
    r = await client.patch("/api/accounts/Sim102", json={"tags": ["50K", " alto riesgo ", "50k"]})
    assert r.status_code == 200 and r.json()["tags"] == ["50K", "alto riesgo"]
    r = await client.put("/api/accounts/tags", json={"accounts": ["Sim102", "Sim103"], "add": ["PA"], "remove": ["alto riesgo"]})
    assert r.status_code == 200 and {a["account_id"]: a["tags"] for a in r.json()} == {"Sim102": ["50K", "PA"], "Sim103": ["PA"]}
    accs = {a["account_id"]: a for a in (await client.get("/api/accounts")).json()}
    assert accs["Sim102"]["tags"] == ["50K", "PA"]
    # presets: por defecto tres; editables; aplicar a varias cuentas toca límites y regla
    r = await client.get("/api/ui/risk-presets")
    assert [p["id"] for p in r.json()["presets"]] == ["conservador", "moderado", "agresivo"]
    bad = r.json()["presets"] + [dict(r.json()["presets"][0])]
    assert (await client.put("/api/ui/risk-presets", json={"presets": bad})).status_code == 422
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101", "multiplier": 3})
    r = await client.post("/api/risk/preset", json={"preset_id": "conservador", "accounts": ["Sim102", "Sim103"]})
    assert r.status_code == 200 and r.json()["applied"] == ["Sim102", "Sim103"]
    lim = container.risk.limits["Sim102"]
    assert lim.max_daily_loss == 500 and lim.max_position_size == 2 and lim.drawdown_buffer == 250
    rule = next(x for x in container.replication.rules if x.follower_account == "Sim102")
    assert rule.multiplier == 1.0 and rule.entry_mode == "limit"
    # consejos: sin límites, desincronizada, racha perdedora
    container.bridge.positions[("Sim102", "NQ 12-26")] = 4     # la maestra está plana: desincronizada
    await container.accounts.sync_once()
    await container.sync.check()
    import time
    container.sync.grace_s = 0
    await container.sync.check()
    day = container.performance.day_key(__import__("datetime").datetime.now())
    for i in range(3):
        container.store.save_trade({"account_id": "Sim101", "day": day, "symbol": "NQ 12-26", "side": "long", "quantity": 1, "entry_price": 1, "exit_price": 0, "pnl": -100, "commissions": 4,
                                    "opened_at": f"2026-01-01T10:0{i}:00", "closed_at": f"2026-01-01T10:0{i}:30", "fills": 2})
    r = await client.get("/api/advice")
    assert r.status_code == 200
    codes = {(a["account_id"], a["code"]) for a in r.json()["advice"]}
    assert ("Sim101", "NO_LIMITS") in codes and ("Sim101", "LOSING_STREAK") in codes
    assert r.json()["counts"]["warn"] >= 2


async def test_oco_entry_partial_close_templates_and_pending_trail(client: AsyncClient, container):
    b = container.bridge
    px = b._px["NQ 12-26"]
    # OCO de entrada: compra stop arriba / venta stop abajo con stop automático; quedan vivas y enlazadas
    r = await client.post("/api/trade/oco", json={"client_id": "oco-1", "account": "Sim101", "symbol": "NQ 12-26", "quantity": 1, "sl_ticks": 40,
                                                 "legs": [{"action": "BUY", "order_type": "STOPMARKET", "stop_price": px + 10}, {"action": "SELL", "order_type": "STOPMARKET", "stop_price": px - 10}],
                                                 "trail": {"mode": "fixed", "distance_ticks": 30}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["legs"]) == 2 and d["trail_pending"] and set(b.oco_groups.values()) >= {"TPM-OCO-oco-1"}
    await container.accounts.sync_once()
    acc = next(a for a in container.accounts.all() if a.account_id == "Sim101")
    assert sorted(o.action for o in acc.working_orders) == ["BUY", "SELL"]
    # brackets en una orden límite: ya no se rechaza
    r = await client.post("/api/trade/order", json={"client_id": "lim-br", "account": "Sim102", "symbol": "ES 12-26", "action": "BUY", "quantity": 1, "order_type": "LIMIT", "limit_price": 5000, "sl_ticks": 20, "tp_ticks": 40})
    assert r.status_code == 200
    # entrada a mercado con trailing automático: el trailing arranca cuando existe la posición
    r = await client.post("/api/trade/order", json={"client_id": "mkt-trail", "account": "Sim103", "symbol": "NQ 12-26", "action": "BUY", "quantity": 4, "order_type": "MARKET", "sl_ticks": 40,
                                                   "trail": {"mode": "fixed", "distance_ticks": 20}})
    assert r.status_code == 200 and r.json()["trail_pending"]
    await container.accounts.sync_once()
    await container.trade.apply_pending_trails()
    assert container.trail.get("Sim103", "NQ 12-26") is not None
    # cierre parcial: 2 de 4; el stop pasa a 2 contratos
    r = await client.post("/api/trade/close", json={"client_id": "close-half", "account": "Sim103", "symbol": "NQ 12-26", "quantity": 2})
    assert r.status_code == 200 and r.json()["remaining"] == 2 and r.json()["quantity"] == 2
    await container.accounts.sync_once()
    acc = next(a for a in container.accounts.all() if a.account_id == "Sim103")
    assert acc.open_positions[0].quantity == 2 and all(o.quantity == 2 for o in acc.working_orders)
    # plantillas
    tpl = [{"id": "nq-scalp", "name": "NQ scalp", "symbol": "NQ 12-26", "quantity": 2, "order_type": "MARKET", "sl_ticks": 40, "tp_ticks": 80, "trail": {"mode": "adaptive", "distance_ticks": 40}}]
    assert (await client.put("/api/ui/trade-templates", json={"templates": tpl})).status_code == 200
    assert (await client.get("/api/ui/trade-templates")).json()["templates"][0]["trail"]["mode"] == "adaptive"
    assert (await client.put("/api/ui/trade-templates", json={"templates": tpl + tpl})).status_code == 422
    # velas para el gráfico
    await b.emit_price("NQ 12-26", move=0.0)
    r = await client.get("/api/market/bars?symbol=NQ%2012-26&n=50")
    assert r.status_code == 200 and isinstance(r.json()["bars"], list) and r.json()["bars"][-1]["c"] == b._px["NQ 12-26"] and r.json()["last"] == b._px["NQ 12-26"] and r.json()["n_bars"] >= 1
