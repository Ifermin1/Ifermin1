from httpx import AsyncClient

from tests.conftest import TOKEN


async def test_requires_token(client: AsyncClient):
    r = await client.get("/api/health", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    r = await client.get(f"/api/health?token={TOKEN}", headers={})
    assert r.status_code == 200


async def test_health_and_accounts(client: AsyncClient):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "mock"
    assert body["bridge"]["connected"] is True
    r = await client.get("/api/accounts")
    assert r.status_code == 200


async def test_rules_crud(client: AsyncClient):
    r = await client.post("/api/rules", json={"master_account": "Sim101", "follower_account": "Sim102",
                                              "multiplier": 2, "symbol_filter": "nq 12-26"})
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["symbol_filter"] == "NQ 12-26"

    r = await client.patch(f"/api/rules/{rule['id']}", json={"enabled": False, "multiplier": 3})
    assert r.status_code == 200
    assert r.json()["enabled"] is False and r.json()["multiplier"] == 3

    r = await client.get("/api/rules")
    assert len(r.json()) == 1

    r = await client.delete(f"/api/rules/{rule['id']}")
    assert r.status_code == 204
    r = await client.delete(f"/api/rules/{rule['id']}")
    assert r.status_code == 404

    r = await client.post("/api/rules", json={"master_account": "A", "follower_account": "B", "multiplier": 0})
    assert r.status_code == 422


async def test_mock_event_flows_to_audit(client: AsyncClient):
    await client.post("/api/rules", json={"master_account": "Sim101", "follower_account": "Sim102"})
    r = await client.post("/api/mock/master-event", json={"quantity": 3, "symbol": "ES 12-26"})
    assert r.status_code == 200
    r = await client.get("/api/audit?event_type=REPLICATED")
    assert r.json()[0]["target_account"] == "Sim102"
    assert "3 ES 12-26" in r.json()[0]["message"]


async def test_kill_switch_and_limits(client: AsyncClient):
    r = await client.post("/api/risk/kill-switch", json={"active": True, "reason": "pánico"})
    assert r.json()["kill_switch"] is True and r.json()["kill_switch_reason"] == "pánico"
    r = await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_position_size": 1})
    assert r.status_code == 200
    r = await client.get("/api/risk")
    assert r.json()["limits"][0]["account_id"] == "Sim102"
    r = await client.post("/api/risk/kill-switch", json={"active": False})
    assert r.json()["kill_switch"] is False


async def test_quick_link(client: AsyncClient):
    r = await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101", "multiplier": 2})
    assert r.status_code == 200 and r.json()["multiplier"] == 2
    rule_id = r.json()["id"]
    r = await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101", "multiplier": 3, "enabled": False})
    assert r.json()["id"] == rule_id and r.json()["enabled"] is False  # misma regla, actualizada
    assert len((await client.get("/api/rules")).json()) == 1
    r = await client.put("/api/accounts/Sim101/link", json={"master_account": "Sim101"})
    assert r.status_code == 422
    r = await client.delete("/api/accounts/Sim102/link?master_account=Sim101")
    assert r.status_code == 204
    assert (await client.get("/api/rules")).json() == []
    r = await client.post("/api/rules", json={"master_account": "A", "follower_account": "a"})
    assert r.status_code == 422


async def test_account_management(client: AsyncClient, container):
    container.bridge.disconnected.add("Sim103")
    await container.accounts.sync_once()
    accts = {a["account_id"]: a for a in (await client.get("/api/accounts")).json()}
    assert accts["Sim103"]["connected"] is False and accts["Sim101"]["connected"] is True
    assert accts["Sim101"]["connection"] == "Simulación"
    # política auto: desconectada => oculta; al conectarse se activa sola
    assert accts["Sim103"]["enabled"] is False and accts["Sim103"]["enabled_source"] == "auto"
    container.bridge.disconnected.clear()
    await container.accounts.sync_once()
    assert container.accounts.accounts["Sim103"].enabled is True
    container.bridge.disconnected.add("Sim103")
    await container.accounts.sync_once()
    assert container.accounts.accounts["Sim103"].enabled is False
    # fijada a mano: se mantiene activa aunque esté desconectada
    r = await client.patch("/api/accounts/Sim103", json={"enabled": True})
    assert r.json()["enabled_source"] == "user"
    await container.accounts.sync_once()
    assert container.accounts.accounts["Sim103"].enabled is True
    r = await client.patch("/api/accounts/Sim103", json={"auto": True})
    assert r.json()["enabled"] is False and r.json()["enabled_source"] == "auto"

    r = await client.patch("/api/accounts/Sim103", json={"enabled": False, "alias": "Eval MFF"})
    assert r.json()["enabled"] is False and r.json()["alias"] == "Eval MFF"
    # persistido: un servicio nuevo sobre el mismo store lo recuerda
    from tradepilot.services.account_service import AccountService
    again = AccountService(container.bridge, container.bus, container.store)
    assert again.accounts["Sim103"].enabled is False and again.accounts["Sim103"].alias == "Eval MFF"
    assert again.accounts["Sim103"].reported is False

    # una cuenta desactivada no recibe copias
    await client.put("/api/accounts/Sim103/link", json={"master_account": "Sim101"})
    await client.post("/api/mock/master-event", json={"quantity": 1})
    last = (await client.get("/api/audit?event_type=BLOCKED")).json()[0]
    assert "desactivada" in last["message"]

    # solo se olvidan cuentas que ya no se reportan
    r = await client.delete("/api/accounts/Sim103")
    assert r.status_code == 409
    container.bridge.accounts.pop("Sim103")
    await container.accounts.sync_once()
    assert (await client.delete("/api/accounts/Sim103")).status_code == 204


def test_parse_accounts_old_and_new_format():
    from tradepilot.infrastructure.brokers.ninja_zmq import NinjaZmqBridge
    old = NinjaZmqBridge.parse_accounts("Sim101|50000.00;Sim102|25000.00")
    assert [(a.account_id, a.balance, a.connected) for a in old] == [("Sim101", 50000.0, None), ("Sim102", 25000.0, None)]
    new = NinjaZmqBridge.parse_accounts("Sim101|50000.00|Connected|MFF;APEX-1|0.00|Disconnected|APEX TRADOVATE;basura;x|nan|")
    assert [(a.account_id, a.connected, a.connection) for a in new][:2] == [("Sim101", True, "MFF"), ("APEX-1", False, "APEX TRADOVATE")]


async def test_addon_upgrade_detected_from_heartbeat(container):
    """Si el engine arrancó con el addon antiguo y luego se actualiza, vuelve a pedir GET_ACCOUNTS_ALL."""
    from tradepilot.core.events import EventBus
    from tradepilot.infrastructure.brokers.ninja_zmq import NinjaZmqBridge
    b = NinjaZmqBridge(EventBus())
    try:
        b._supports_all = False
        b._retry_all_at = 10**12
        container.replication.bridge = b
        await container.replication.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "version": "1.1"})
        assert b._supports_all is True and b.health.addon_version == "1.1"
    finally:
        await b.stop()   # sin esto el contexto ZMQ bloquea al recolectarse


async def test_set_master(client: AsyncClient, container):
    r = await client.post("/api/master", json={"account": "Sim102"})
    assert r.status_code == 200 and r.json()["master_account"] == "Sim102"
    assert container.bridge.health.master_account == "Sim102"
    assert (await client.get("/api/health")).json()["bridge"]["master_account"] == "Sim102"
    r = await client.post("/api/master", json={"account": "NoExiste"})
    assert r.status_code == 400 and "desconocida" in r.json()["detail"]
    assert (await client.get("/api/audit?event_type=MASTER_CHANGED")).json()[0]["source_account"] == "Sim102"


async def test_flatten_and_kill_with_flatten(client: AsyncClient, container):
    b = container.bridge
    b.positions[("Sim102", "NQ 12-26")] = 2
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101"})
    r = await client.post("/api/accounts/Sim102/flatten", json={"reason": "prueba"})
    assert r.status_code == 200 and b.flattened == ["Sim102"] and b.positions[("Sim102", "NQ 12-26")] == 0
    types = [a.event_type for a in container.audit.recent(4)]
    assert "FLATTEN" in types and "FLATTENED" in types
    b.positions[("Sim102", "NQ 12-26")] = 1
    b.positions[("Sim101", "NQ 12-26")] = 2
    r = await client.post("/api/risk/kill-switch", json={"active": True, "reason": "pánico", "flatten": True})
    assert r.json()["kill_switch"] is True and sorted(b.flattened) == ["Sim101", "Sim102", "Sim102"]   # maestra incluida
    assert b.positions[("Sim101", "NQ 12-26")] == 0
    await client.post("/api/risk/kill-switch", json={"active": False})
    r = await client.post("/api/risk/flatten-all", json={"include_master": True})
    assert r.status_code == 200 and "Sim101" in r.json()["results"]


async def test_desync_detection_blocks_entries_and_resync(client: AsyncClient, container):
    """La seguidora tiene 3 y la maestra 1 (x1): tras el periodo de gracia se marca DESYNC,
    se bloquean copias que aumenten exposición, pasan las que la reducen, y 'igualar' manda la diferencia."""
    container.sync.grace = 0
    container.bridge.health.master_account = "Sim101"
    b = container.bridge
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101"})
    b.positions[("Sim101", "NQ 12-26")] = 1
    b.positions[("Sim102", "NQ 12-26")] = 3
    await container.accounts.sync_once()
    await container.accounts.sync_once()
    acc = {a["account_id"]: a for a in (await client.get("/api/accounts")).json()}["Sim102"]
    assert acc["desync"] is True and "esperado +1" in acc["desync_detail"]
    # copia que aumenta exposición (BUY estando largo) -> bloqueada
    b.fill_orders = False
    await client.post("/api/mock/master-event", json={"action": "BUY", "quantity": 1, "symbol": "NQ 12-26"})
    last = (await client.get("/api/audit?limit=1")).json()[0]
    assert last["event_type"] == "BLOCKED" and "desincronizada" in last["message"]
    # copia que reduce (SELL) -> pasa
    await client.post("/api/mock/master-event", json={"action": "SELL", "quantity": 1, "symbol": "NQ 12-26"})
    assert (await client.get("/api/audit?limit=1")).json()[0]["event_type"] == "REPLICATED"
    # igualar: real 3, esperado 1 -> SELL 2
    b.fill_orders = True
    r = await client.post("/api/accounts/Sim102/resync")
    assert r.json()["sent"] == [{"symbol": "NQ 12-26", "action": "SELL", "quantity": 2}]
    await container.accounts.sync_once()
    acc = {a["account_id"]: a for a in (await client.get("/api/accounts")).json()}["Sim102"]
    assert acc["desync"] is False
    assert any(a.event_type == "RESYNC" for a in container.audit.recent(5))


async def test_stop_rejected_closes_follower(client: AsyncClient, container):
    b = container.bridge
    b.positions[("Sim102", "NQ 12-26")] = 2
    await container.accounts.sync_once()
    await container.replication.process_master_event({"msg_type": "ORDER_STATUS", "account": "Sim102", "action": "SELL",
        "symbol": "NQ 12-26", "quantity": 2, "order_type": "STOPMARKET", "state": "Rejected", "order_id": "f1",
        "master_order_id": "m1", "error": "OrderRejected", "native_error": "max qty", "timestamp": "x"})
    import asyncio
    await asyncio.sleep(0.05)
    assert b.flattened == ["Sim102"]
    types = [a.event_type for a in container.audit.recent(6)]
    assert "NAKED_CLOSE" in types and "FOLLOWER_REJECTED" in types


async def test_seq_gaps_and_restart_detected(container):
    rep = container.replication
    for seq in (1, 2, 5, 6, 1):
        await rep.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "seq": seq})
    assert rep.stats["seq_gaps"] == 2 and rep.stats["addon_restarts"] == 1
    types = [a.event_type for a in container.audit.recent(5)]
    assert "GAP" in types and "ADDON_RESTART" in types


async def test_journal_records_in_and_out(container, tmp_path):
    from tradepilot.infrastructure.persistence.journal import Journal
    j = Journal(str(tmp_path / "journal"))
    container.replication.journal = j
    await container.replication.start()
    container.replication.add_rule("Sim101", "Sim102")
    await container.bridge.emit_master_event(quantity=1, symbol="NQ 12-26")
    j.close()
    lines = [l for f in (tmp_path / "journal").glob("*.jsonl") for l in f.read_text().splitlines()]
    import json
    dirs = [json.loads(l)["dir"] for l in lines]
    assert "in" in dirs and "out" in dirs


async def test_master_flatten_fills_are_not_copied(client: AsyncClient, container):
    """Cerrar TODO: el fill de salida de la maestra no debe copiarse a las seguidoras ya cerradas."""
    b = container.bridge
    b.health.master_account = "Sim101"
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101"})
    b.positions[("Sim101", "NQ 12-26")] = 2
    b.positions[("Sim102", "NQ 12-26")] = 2
    await container.accounts.sync_once()
    sent_before = len(b.sent_orders)
    r = await client.post("/api/risk/flatten-all", json={"include_master": True})
    assert set(r.json()["results"]) == {"Sim101", "Sim102"}
    # NinjaTrader reporta el fill del cierre de la maestra como EXECUTION normal
    await client.post("/api/mock/master-event", json={"action": "SELL", "quantity": 2, "symbol": "NQ 12-26"})
    assert len(b.sent_orders) == sent_before, "el fill del cierre de la maestra se copió a la seguidora"
    last = (await client.get("/api/audit?limit=1")).json()[0]
    assert last["event_type"] == "SKIPPED" and "cierre de emergencia" in last["message"]
    assert b.positions[("Sim102", "NQ 12-26")] == 0


async def test_daily_loss_limit_halts_and_flattens(client: AsyncClient, container):
    b = container.bridge
    b.positions[("Sim102", "NQ 12-26")] = 2
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101"})
    await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_daily_loss": 500})
    # 80 % -> aviso; 100 % -> pausa + cierre
    b.pnl = {"Sim102": -420.0}
    await container.accounts.sync_once()
    assert any(a.event_type == "DAILY_LOSS_WARNING" for a in container.audit.recent(5))
    assert container.risk.limits["Sim102"].trading_halted is False
    b.pnl = {"Sim102": -510.0}
    await container.accounts.sync_once()
    lim = container.risk.limits["Sim102"]
    assert lim.trading_halted and lim.halted_reason == "daily_loss" and b.flattened == ["Sim102"]
    assert any(a.event_type == "DAILY_LOSS_LIMIT" for a in container.audit.recent(6))
    # con el P&L aún por debajo del límite no se puede reanudar
    r = await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_daily_loss": 500, "trading_halted": False})
    assert r.status_code == 409 and "no se reanuda" in r.json()["detail"]
    # sigue bloqueada aunque el P&L mejore; el usuario la reanuda a mano
    b.pnl = {"Sim102": -100.0}
    await container.accounts.sync_once()
    ok, reason = container.risk.allows("Sim102", 1, "NQ 12-26", "BUY")
    assert not ok and "pérdida diaria" in reason
    r = await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_daily_loss": 500, "trading_halted": False})
    assert r.json()["trading_halted"] is False and r.json()["halted_reason"] == ""
    assert container.risk.allows("Sim102", 1, "NQ 12-26", "BUY")[0]


async def test_schedule_window_and_scheduled_flatten(client: AsyncClient, container):
    from datetime import datetime
    b = container.bridge
    b.health.master_account = "Sim101"
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101"})
    r = await client.put("/api/risk/schedule", json={"enabled": True, "window_start": "09:30", "flatten_at": "15:55", "include_master": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    fake_now = datetime(2026, 9, 16, 9, 0)
    container.risk.now = lambda: fake_now
    ok, reason = container.risk.allows("Sim102", 1)
    assert not ok and "09:30" in reason
    fake_now = datetime(2026, 9, 16, 10, 0)
    assert container.risk.allows("Sim102", 1)[0]
    # a la hora de cierre: flatten de todas y bloqueo hasta mañana
    b.positions[("Sim101", "NQ 12-26")] = 1
    b.positions[("Sim102", "NQ 12-26")] = 1
    fake_now = datetime(2026, 9, 16, 15, 56)
    await container.accounts.sync_once()
    assert sorted(b.flattened) == ["Sim101", "Sim102"]
    assert any(a.event_type == "SCHEDULED_FLATTEN" for a in container.audit.recent(8))
    ok, reason = container.risk.allows("Sim102", 1)
    assert not ok and "sesión cerrada" in reason
    await container.accounts.sync_once()
    assert len(b.flattened) == 2, "el cierre programado se repitió"
    # reabrir a mano
    r = await client.post("/api/risk/reopen")
    assert r.json()["session_closed"] is False
    # al día siguiente dentro de ventana se copia de nuevo
    fake_now = datetime(2026, 9, 17, 10, 0)
    assert container.risk.allows("Sim102", 1)[0]
    r = await client.put("/api/risk/schedule", json={"enabled": True, "flatten_at": "9:5"})
    assert r.status_code == 422


async def test_resulting_exposure_limit(client: AsyncClient, container):
    b = container.bridge
    b.positions[("Sim102", "NQ 12-26")] = 2
    await container.accounts.sync_once()
    await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_position_size": 3})
    assert container.risk.allows("Sim102", 1, "NQ 12-26", "BUY")[0]            # 2 -> 3 ok
    ok, reason = container.risk.allows("Sim102", 2, "NQ 12-26", "BUY")          # 2 -> 4 no
    assert not ok and "posición resultante 4" in reason
    assert container.risk.allows("Sim102", 3, "NQ 12-26", "SELL")[0]           # reduce/invierte a -1 ok
    ok, _ = container.risk.allows("Sim102", 4, "NQ 12-26", "SELL")             # por orden > 3
    assert not ok


async def test_heartbeat_watchdog(container):
    from datetime import datetime, timedelta
    container.bridge.health.mode = "ninja"
    container.bridge.health.last_heartbeat = datetime.now() - timedelta(seconds=60)
    await container.risk.check()
    assert container.risk.addon_silent is True
    assert any(a.event_type == "ADDON_SILENT" for a in container.audit.recent(3))
    # autocuración: el addon responde a PING pero no manda eventos -> se reconecta el canal de eventos
    assert getattr(container.bridge, "resubscribes", 0) == 1
    assert any(a.event_type == "RESUBSCRIBE" for a in container.audit.recent(3))
    container.bridge.health.last_heartbeat = datetime.now()
    await container.risk.check()
    assert container.risk.addon_silent is False
