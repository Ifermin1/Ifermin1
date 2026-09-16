import asyncio

import pytest
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
    # quitar los límites de una cuenta (16/9: la tabla no tenía cómo editarlos ni eliminarlos)
    r = await client.delete("/api/risk/limits/Sim102")
    assert r.status_code == 204
    assert (await client.get("/api/risk")).json()["limits"] == []
    assert (await client.delete("/api/risk/limits/Sim102")).status_code == 404


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


async def test_daily_profit_target_halts_and_flattens(client: AsyncClient, container):
    b = container.bridge
    b.positions[("Sim102", "NQ 12-26")] = 1
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101"})
    r = await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_daily_loss": 500, "max_daily_profit": 1000})
    assert r.status_code == 200 and r.json()["max_daily_profit"] == 1000
    # 80 % -> aviso; objetivo -> pausa + cierre para asegurar la ganancia
    b.pnl = {"Sim102": 850.0}
    await container.accounts.sync_once()
    assert any(a.event_type == "DAILY_PROFIT_WARNING" for a in container.audit.recent(5))
    assert container.risk.limits["Sim102"].trading_halted is False
    b.pnl = {"Sim102": 1020.0}
    await container.accounts.sync_once()
    lim = container.risk.limits["Sim102"]
    assert lim.trading_halted and lim.halted_reason == "daily_profit" and b.flattened == ["Sim102"]
    assert any(a.event_type == "DAILY_PROFIT_TARGET" for a in container.audit.recent(6))
    ok, reason = container.risk.allows("Sim102", 1, "NQ 12-26", "BUY")
    assert not ok and "objetivo de ganancia" in reason
    # con el P&L aún por encima del objetivo no se reanuda; subiendo el objetivo sí
    r = await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_daily_profit": 1000, "trading_halted": False})
    assert r.status_code == 409 and "por encima del objetivo" in r.json()["detail"]
    r = await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_daily_profit": 2000, "trading_halted": False})
    assert r.status_code == 200 and r.json()["trading_halted"] is False and r.json()["halted_reason"] == ""
    assert container.risk.allows("Sim102", 1, "NQ 12-26", "BUY")[0]
    # el límite persiste en SQLite
    assert next(l for l in container.store.get_risk_limits() if l.account_id == "Sim102").max_daily_profit == 2000


async def test_stale_event_channel_is_reconnected_and_followers_rewatched(client: AsyncClient, container):
    from datetime import datetime, timedelta
    b = container.bridge
    b.health.master_account = "Sim101"
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101"})
    assert b.watched == ["Sim102"]
    # el canal de eventos vio el arranque "A"; por comandos el addon dice que ya es el arranque "B" y lleva 500 mensajes
    b.health.addon_boot = "A"
    b.health.last_msg_in = datetime.now() - timedelta(seconds=10)
    b.boot, b.seq = "B", 500
    await container.accounts.sync_once()          # 1ª comprobación sospechosa
    assert b.health.resubscribes == 0
    await container.accounts.sync_once()          # 2ª seguida -> reconectar
    assert b.health.resubscribes == 1
    types = [a.event_type for a in container.audit.recent(6)]
    assert "RESUBSCRIBE" in types and "ADDON_RECOVERY" in types
    assert b.watched == ["Sim102", "Sim102"], "tras reconectar hay que volver a pedir WATCH de la seguidora"
    # con mensajes recientes no se toca nada aunque el seq por comandos vaya por delante
    b.health.last_msg_in = datetime.now()
    await container.accounts.sync_once(); await container.accounts.sync_once()
    assert b.health.resubscribes == 1


async def test_addon_restart_seen_on_events_rewatches_followers(client: AsyncClient, container):
    b = container.bridge
    b.health.master_account = "Sim101"
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101"})
    await b.emit_master_event(seq=50, action="BUY", quantity=1, symbol="NQ 12-26")
    await b.emit_master_event(seq=1, action="SELL", quantity=1, symbol="NQ 12-26")
    types = [a.event_type for a in container.audit.recent(12)]
    assert "ADDON_RESTART" in types and "ADDON_RECOVERY" in types
    assert b.watched == ["Sim102", "Sim102"]
    assert container.replication.stats["addon_restarts"] == 1
    # y la operación posterior al reinicio se copia igual
    assert any(a.event_type == "REPLICATED" for a in container.audit.recent(6))


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


async def test_link_with_execution_options_and_persistence(client: AsyncClient, container):
    r = await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101", "multiplier": 10, "target_root": "mnq",
                                                             "entry_mode": "limit", "tolerance_ticks": 2})
    assert r.status_code == 200 and r.json()["target_root"] == "MNQ" and r.json()["entry_mode"] == "limit"
    # quitar el mapeo explícitamente
    r = await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101", "multiplier": 10, "target_root": ""})
    assert r.json()["target_root"] is None and r.json()["entry_mode"] == "limit"
    r = await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101", "entry_mode": "nope"})
    assert r.status_code == 422
    from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
    rules = container.store.get_all_rules()
    assert rules[0].entry_mode == "limit" and rules[0].tolerance_ticks == 2


async def test_orders_go_through_confirmed_channel_with_fallback():
    """Con addon >= 1.9 la orden va por 5557 y se confirma; si el addon es antiguo, se cae a 5556 sin confirmación;
    si el addon no responde, la orden se reintenta y luego falla con error en vez de darse por enviada."""
    import threading
    import zmq as pyzmq
    from tradepilot.core.config import settings
    from tradepilot.core.events import EventBus
    from tradepilot.infrastructure.brokers.ninja_zmq import NinjaZmqBridge

    ctx = pyzmq.Context()
    rep = ctx.socket(pyzmq.REP); rep.setsockopt(pyzmq.LINGER, 0); rep.bind(f"tcp://127.0.0.1:{settings.ZMQ_SYNC_PORT}")
    sub = ctx.socket(pyzmq.SUB); sub.setsockopt(pyzmq.LINGER, 0); sub.bind(f"tcp://127.0.0.1:{settings.ZMQ_FOLLOWER_PORT}")
    sub.setsockopt_string(pyzmq.SUBSCRIBE, "")
    received: list[str] = []
    mode = {"reply": "OK|EXECUTION"}          # None = no contestar (addon colgado)
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            if rep.poll(50):
                msg = rep.recv_string(); received.append(msg)
                if mode["reply"] is None:
                    # REP no puede quedarse sin contestar: recreamos el socket para simular un addon mudo
                    continue
                rep.send_string(mode["reply"])
    t = threading.Thread(target=serve, daemon=True); t.start()

    b = NinjaZmqBridge(EventBus())
    b.order_timeout_ms = 300
    try:
        await b.start()
        await asyncio.sleep(0.2)
        await b.send_order("Sim102", "BUY", "NQ 12-26", 1, "MARKET", "m1")
        assert received[-1].startswith("ORDER|") and '"master_order_id": "m1"' in received[-1]
        assert b.health.order_channel == "req" and b.health.orders_confirmed == 1
        # el addon rechaza -> error claro
        mode["reply"] = "ERROR|follower desconocido: X"
        with pytest.raises(RuntimeError, match="rechazó"):
            await b.send_order("X", "BUY", "NQ 12-26", 1, "MARKET", "m2")
        # addon antiguo -> se cae a 5556
        mode["reply"] = "ERROR|unknown request"
        await b.send_order("Sim102", "BUY", "NQ 12-26", 1, "MARKET", "m3")
        assert b.health.order_channel == "pub" and b._orders_via_req is False
        # PUB/SUB puede perder el primer mensaje (slow joiner): justamente por eso el canal normal es el confirmado
        got = []
        for _ in range(5):
            if sub.poll(300):
                got.append(sub.recv_string()); break
            await b.send_order("Sim102", "BUY", "NQ 12-26", 1, "MARKET", "m3")
        assert got and '"master_order_id": "m3"' in got[0]
        # el heartbeat anuncia addon 1.9 -> vuelve al canal con confirmación
        b.note_addon_version("1.9")
        assert b._orders_via_req is True
    finally:
        await b.stop()
        stop.set(); t.join(timeout=2)
        rep.close(); sub.close(); ctx.term()


async def test_order_without_confirmation_fails_instead_of_silently_sent():
    from tradepilot.core.events import EventBus
    from tradepilot.infrastructure.brokers.ninja_zmq import NinjaZmqBridge
    b = NinjaZmqBridge(EventBus())     # nadie escucha en 5557
    b.order_timeout_ms = 200
    try:
        await b.start()
        with pytest.raises(RuntimeError, match="no confirmó"):
            await b.send_order("Sim102", "BUY", "NQ 12-26", 1, "MARKET", "m9")
        assert b.health.orders_retried == 1 and b.health.orders_confirmed == 0
    finally:
        await b.stop()


async def test_working_orders_and_pnl_history(client: AsyncClient, container):
    from tradepilot.domain.accounts import WorkingOrder
    from tradepilot.infrastructure.brokers.ninja_zmq import NinjaZmqBridge

    parsed = NinjaZmqBridge.parse_orders(
        "Sim102|k1|m1|SELL|MNQ 12-26|2|0|STOPMARKET|0|24500.25|Working;"
        "Sim102|k2|m1|SELL|MNQ 12-26|2|0|LIMIT|24600|0|Working;"
        "malformed|row;"
        "Sim103|k3|m2|BUY|ES 12-26|1|1|MARKET|0|0|Filled")
    assert [(acc, o.order_id, o.order_type, o.stop_price) for acc, o in parsed] == [
        ("Sim102", "k1", "STOPMARKET", 24500.25), ("Sim102", "k2", "LIMIT", 0.0), ("Sim103", "k3", "MARKET", 0.0)]

    bridge = container.bridge
    bridge.working_orders = parsed[:2]
    container.accounts.pnl_sample_seconds = 0.0
    await container.accounts.sync_once()
    r = await client.get("/api/accounts")
    by_id = {a["account_id"]: a for a in r.json()}
    assert [o["order_id"] for o in by_id["Sim102"]["working_orders"]] == ["k1", "k2"]
    assert by_id["Sim103"]["working_orders"] == []

    bridge.working_orders = []
    await container.accounts.sync_once()
    r = await client.get("/api/accounts")
    assert all(a["working_orders"] == [] for a in r.json())

    r = await client.get("/api/pnl?hours=1")
    assert r.status_code == 200
    body = r.json()
    assert "Sim101" in body and len(body["Sim101"]) >= 1
    ts, pnl = body["Sim101"][-1]
    assert isinstance(ts, str) and isinstance(pnl, (int, float))


async def test_addon_reply_is_reflected_in_audit(client: AsyncClient, container):
    """Si el addon contesta IGNORED la orden no cuenta como replicada (SKIPPED con el motivo); si contesta OK con un
    detalle (fill parcial, copia recreada) ese detalle queda en la línea REPLICATED."""
    await client.post("/api/rules", json={"master_account": "Sim101", "follower_account": "Sim102"})
    b = container.bridge
    b.next_replies.append("IGNORED|sin orden trabajando")
    await client.post("/api/mock/master-event", json={"quantity": 1, "symbol": "NQ 12-26", "action": "BUY", "order_id": "ign1"})
    r = await client.get("/api/audit?limit=5")
    types = [a["event_type"] for a in r.json()]
    assert "REPLICATED" not in types
    skipped = next(a for a in r.json() if a["event_type"] == "SKIPPED")
    assert "el addon no la aplicó (sin orden trabajando)" in skipped["message"] and skipped["target_account"] == "Sim102"
    assert b.sent_orders == []

    b.next_replies.append("OK|EXECUTION_PARTIAL")
    await client.post("/api/mock/master-event", json={"quantity": 2, "symbol": "NQ 12-26", "action": "BUY", "order_id": "part1"})
    r = await client.get("/api/audit?event_type=REPLICATED")
    assert "fill parcial del maestro" in r.json()[0]["message"]
    assert len(b.sent_orders) == 1


async def test_watch_is_retried_until_the_addon_confirms(client: AsyncClient, container):
    b = container.bridge
    b.watch_ok = False
    r = await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101", "multiplier": 1})
    assert r.status_code == 200, r.text
    assert b.watched == ["Sim102"] and "Sim102" not in container.accounts._watched
    await container.accounts.sync_once()
    assert b.watched == ["Sim102", "Sim102"], "sin confirmación se reintenta en el siguiente sync"
    b.watch_ok = True
    await container.accounts.sync_once()
    assert "Sim102" in container.accounts._watched
    await container.accounts.sync_once()
    assert b.watched.count("Sim102") == 3, "una vez confirmado no se insiste"


async def test_phantom_order_is_audited_once(container):
    """16/9 14:23: un "0 Sell STP" en el gráfico. Una orden viva sin nada por ejecutar se avisa una vez con quién la creó."""
    from tradepilot.infrastructure.brokers.ninja_zmq import NinjaZmqBridge
    parsed = NinjaZmqBridge.parse_orders("Sim102|k9|661594272992|SELL|NQ 12-26|1|1|STOPMARKET|0|29468.75|Accepted;"
                                         "Sim102|k1|m1|SELL|NQ 12-26|2|0|STOPMARKET|0|29400|Working")
    container.bridge.working_orders = parsed
    await container.accounts.sync_once()
    await container.accounts.sync_once()
    phantoms = [a for a in container.audit.recent(20) if a.event_type == "PHANTOM_ORDER"]
    assert len(phantoms) == 1 and "661594272992" in phantoms[0].message and "1/1" in phantoms[0].message


async def test_overclose_left_by_the_copier_is_closed_automatically(container):
    """16/9 14:30: la copia del stop ejecutó después de 'cancelarse' y el resto ya se había cerrado a mercado: Sim102 quedó
    corta 1 con la maestra plana. Si la dejó el copiador y persiste, el engine la cierra; si alguien operó a mano, no."""
    b = container.bridge
    rep, sync = container.replication, container.sync
    b.health.master_account = "Sim101"
    rep.add_rule("Sim101", "Sim102", multiplier=2)
    sync.fix_grace = 0.0
    b.positions[("Sim102", "NQ 12-26")] = -1                 # maestra plana, seguidora corta 1
    await container.accounts.sync_once()
    sent_before = len(b.sent_orders)
    await sync.check()                                        # sin actividad del copiador: no se toca (posición manual)
    assert len(b.sent_orders) == sent_before
    rep.last_copy_activity[("sim102", "NQ")] = __import__("time").monotonic()
    await sync.check(); await sync.check()
    fixes = [o for o in b.sent_orders if o["master_order_id"].startswith("FIX-")]
    assert len(fixes) == 1 and fixes[0]["action"] == "BUYTOCOVER" and fixes[0]["quantity"] == 1
    assert any(a.event_type == "OVERCLOSE_FIX" for a in container.audit.recent(5))
    await container.accounts.sync_once()
    assert container.accounts.position("Sim102", "NQ 12-26") == 0
    # invertida respecto a la maestra (maestra larga 1 -> esperado +2; seguidora corta 1): se cierra la corta, no se abre nada
    b.positions[("Sim101", "NQ 12-26")] = 1
    b.positions[("Sim102", "NQ 12-26")] = -1
    await container.accounts.sync_once()
    rep.last_copy_activity[("sim102", "NQ")] = __import__("time").monotonic()
    sync._fixed_at.clear()
    await sync.check(); await sync.check()
    fixes = [o for o in b.sent_orders if o["master_order_id"].startswith("FIX-")]
    assert len(fixes) == 2 and fixes[-1]["action"] == "BUYTOCOVER" and fixes[-1]["quantity"] == 1
    # un fill manual posterior a la última copia: la posición es de la persona, no del copiador -> no se toca
    b.positions[("Sim102", "NQ 12-26")] = -1
    await container.accounts.sync_once()
    sync._fixed_at.clear()
    rep.last_manual_fill[("sim102", "NQ")] = __import__("time").monotonic()
    await sync.check(); await sync.check()
    assert len([o for o in b.sent_orders if o["master_order_id"].startswith("FIX-")]) == 2


async def test_copies_of_one_event_go_out_in_a_single_batch(client: AsyncClient, container):
    """16/9 15:18 con 11 seguidoras: las copias salían una a una (una ida y vuelta por cuenta) y la entrada tardó
    1,6-3,1 s en llenarse. Ahora todas las copias de un evento van en una sola petición al addon."""
    b = container.bridge
    b.health.master_account = "Sim101"
    for acc in ("Sim102", "Sim103"):
        await client.put(f"/api/accounts/{acc}/link", json={"master_account": "Sim101"})
    await b.emit_master_event(seq=70, action="BUY", quantity=1, symbol="NQ 12-26")
    assert b.batches[-1] == 2 and [o["account"] for o in b.sent_orders[-2:]] == ["Sim102", "Sim103"]
    reps = [a for a in container.audit.recent(10) if a.event_type == "REPLICATED"]
    assert len(reps) == 2 and all("dispatch_ms" in (a.details or {}) for a in reps)
    assert container.replication.stats["fanout_ms_last"] is not None


async def test_lifecycle_states_are_not_audited_but_terminal_ones_are(container):
    rep = container.replication
    base = {"msg_type": "ORDER_STATUS", "account": "Sim102", "action": "SELL", "quantity": 2, "symbol": "NQ 12-26",
            "order_id": "k1", "master_order_id": "m1"}
    before = len([a for a in container.audit.recent(50) if a.event_type == "FOLLOWER_STATUS"])
    for st in ("Initialized", "Submitted", "Accepted", "ChangePending", "ChangeSubmitted", "CancelPending", "CancelSubmitted"):
        await rep.process_master_event({**base, "state": st})
    assert len([a for a in container.audit.recent(50) if a.event_type == "FOLLOWER_STATUS"]) == before
    for st in ("Working", "PartFilled", "Filled"):
        await rep.process_master_event({**base, "state": st})
    assert len([a for a in container.audit.recent(50) if a.event_type == "FOLLOWER_STATUS"]) == before + 3
    rep.audit_lifecycle = True
    await rep.process_master_event({**base, "state": "Submitted", "order_id": "k2"})
    assert container.audit.recent(1)[0].event_type == "FOLLOWER_STATUS"


async def test_follower_fill_reports_broker_side_latency(container):
    rep = container.replication
    b = container.bridge
    b.health.master_account = "Sim101"
    rep.add_rule("Sim101", "Sim102")
    ev = {"msg_type": "EXECUTION", "account": "Sim101", "action": "BUY", "symbol": "NQ 12-26", "quantity": 1, "price": 20000.0,
          "order_type": "MARKET", "state": "Filled", "order_id": "M77", "execution_id": "e77", "timestamp": "2026-09-16T15:18:46.500"}
    await rep.process_master_event(ev)
    await rep.process_master_event({**ev, "account": "Sim102", "order_id": "F77", "master_order_id": "M77", "execution_id": "f77",
                                    "price": 20000.25, "timestamp": "2026-09-16T15:18:46.590"})
    fill = container.audit.recent(1)[0]
    assert fill.event_type == "FOLLOWER_FILL" and fill.details["broker_ms"] == 90 and "en bróker 90 ms" in fill.message
    assert rep.stats["broker_ms_last"] == 90


async def test_zmq_bridge_batches_orders_and_falls_back_on_old_addon(monkeypatch):
    from tradepilot.infrastructure.brokers.ninja_zmq import NinjaZmqBridge
    from tradepilot.core.events import EventBus
    br = NinjaZmqBridge(EventBus())
    br._running = True
    sent: list[str] = []
    replies = ["OK|EXECUTION\x1fIGNORED|la orden del follower ya se ejecutó\x1fERROR|follower desconocido: X"]

    async def fake_request(msg):
        sent.append(msg)
        return replies.pop(0) if replies else "OK|EXECUTION"
    monkeypatch.setattr(br, "_order_request", fake_request)
    orders = [dict(target_account=a, action="BUY", symbol="NQ 12-26", quantity=1, order_type="MARKET", master_order_id="m")
              for a in ("A", "B", "X")]
    out = await br.send_orders(orders)
    assert sent[0].startswith("ORDERS|") and sent[0].count("\x1f") == 2
    assert out[0] == "OK|EXECUTION" and out[1].startswith("IGNORED|") and isinstance(out[2], RuntimeError)
    # addon anterior a 2.5: ERROR|unknown request -> una a una, y el lote se reintenta pasado un minuto
    replies[:] = ["ERROR|unknown request", "OK|EXECUTION", "OK|EXECUTION", "OK|EXECUTION"]
    sent.clear()
    out = await br.send_orders(orders)
    assert sent[0].startswith("ORDERS|") and all(m.startswith("ORDER|") for m in sent[1:]) and len(sent) == 4
    assert out == ["OK|EXECUTION"] * 3 and br._orders_batch is False
    await br.stop()          # cierra los sockets: un contexto ZMQ con sockets abiertos bloquea la salida del proceso


async def test_trailing_drawdown_tracks_peak_warns_and_halts(client: AsyncClient, container):
    """El drawdown dinámico se mide desde el máximo que llegó a valer la cuenta (balance + flotante); el engine avisa al
    80 % y pausa/cierra cuando faltan `drawdown_buffer` USD para el suelo, antes de que el prop firm cierre la cuenta."""
    b = container.bridge
    b.noise = 0.0
    b.accounts["Sim102"] = 25_000.0
    b.positions[("Sim102", "NQ 12-26")] = 2
    await container.accounts.sync_once()
    await container.accounts.set_peak("Sim102", None)   # el arranque sincronizó con ruido: evaluación nueva
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101"})
    r = await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_trailing_drawdown": 1000, "drawdown_buffer": 100})
    assert r.status_code == 200 and r.json()["max_trailing_drawdown"] == 1000 and r.json()["drawdown_mode"] == "intraday"
    await container.accounts.sync_once()
    dd = container.accounts.accounts["Sim102"].drawdown
    assert dd.peak == 25_000 and dd.floor == 24_000 and dd.room == 1000 and dd.pct == 0
    # la cuenta sube: el máximo y el suelo suben con ella (trailing)
    b.accounts["Sim102"] = 26_000.0
    await container.accounts.sync_once()
    dd = container.accounts.accounts["Sim102"].drawdown
    assert dd.peak == 26_000 and dd.floor == 25_000 and dd.equity == 26_000
    # flotante en contra: el máximo no baja, el drawdown crece
    b.unrealized = {"Sim102": -700.0}
    await container.accounts.sync_once()
    dd = container.accounts.accounts["Sim102"].drawdown
    assert dd.peak == 26_000 and dd.drawdown == 700 and dd.room == 300 and dd.pct == 70
    assert not any(a.event_type == "DRAWDOWN_WARNING" for a in container.audit.recent(10))
    b.unrealized = {"Sim102": -850.0}
    await container.accounts.sync_once()
    assert any(a.event_type == "DRAWDOWN_WARNING" for a in container.audit.recent(5))
    assert container.risk.limits["Sim102"].trading_halted is False
    # a 90 del suelo (colchón 100): pausa y cierre
    b.unrealized = {"Sim102": -910.0}
    await container.accounts.sync_once()
    lim = container.risk.limits["Sim102"]
    assert lim.trading_halted and lim.halted_reason == "drawdown" and b.flattened == ["Sim102"]
    ev = next(a for a in container.audit.recent(6) if a.event_type == "DRAWDOWN_LIMIT")
    assert ev.details["floor"] == 25_000 and ev.details["room"] == 90
    assert "límite de drawdown" in container.risk.allows("Sim102", 1, "NQ 12-26", "BUY")[1]
    # sigue pegada al suelo: no se reanuda
    r = await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_trailing_drawdown": 1000, "drawdown_buffer": 100, "trading_halted": False})
    assert r.status_code == 409 and "suelo" in r.json()["detail"]
    # con aire de nuevo, sí
    b.unrealized = {}
    b.accounts["Sim102"] = 25_600.0
    await container.accounts.sync_once()
    r = await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_trailing_drawdown": 1000, "drawdown_buffer": 100, "trading_halted": False})
    assert r.status_code == 200 and r.json()["trading_halted"] is False
    # la API expone el drawdown de todas las cuentas, también sin límite configurado
    accs = {a["account_id"]: a for a in (await client.get("/api/accounts")).json()}
    assert accs["Sim102"]["drawdown"]["peak"] == 26_000 and accs["Sim102"]["drawdown"]["floor"] == 25_000
    assert accs["Sim101"]["drawdown"]["limit"] == 0 and accs["Sim101"]["drawdown"]["floor"] is None and accs["Sim101"]["drawdown"]["peak"] > 0


async def test_drawdown_floor_cap_locks_and_closed_mode_ignores_floating(client: AsyncClient, container):
    b = container.bridge
    b.noise = 0.0
    b.accounts["Sim102"] = 50_000.0
    await container.accounts.sync_once()
    await container.accounts.set_peak("Sim102", None)
    await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_trailing_drawdown": 2500, "drawdown_floor_cap": 50_100})
    await container.accounts.sync_once()
    dd = container.accounts.accounts["Sim102"].drawdown
    assert dd.floor == 47_500 and not dd.locked
    b.accounts["Sim102"] = 53_000.0     # suelo natural 50 500 > tope 50 100: se bloquea (regla APEX)
    await container.accounts.sync_once()
    dd = container.accounts.accounts["Sim102"].drawdown
    assert dd.floor == 50_100 and dd.locked and dd.room == 2900
    # modo "solo cerrado": el flotante no sube el máximo ni cuenta como caída
    await client.put("/api/risk/limits", json={"account_id": "Sim102", "max_trailing_drawdown": 2500, "drawdown_mode": "closed"})
    b.unrealized = {"Sim102": 4000.0}
    await container.accounts.sync_once()
    dd = container.accounts.accounts["Sim102"].drawdown
    assert dd.mode == "closed" and dd.peak == 53_000 and dd.drawdown == 0 and dd.floor == 50_500
    b.unrealized = {"Sim102": -2000.0}
    await container.accounts.sync_once()
    dd = container.accounts.accounts["Sim102"].drawdown
    assert dd.drawdown == 0 and dd.equity == 51_000 and container.risk.limits["Sim102"].trading_halted is False


async def test_peak_survives_restart_and_can_be_reset_or_set(client: AsyncClient, container):
    b = container.bridge
    b.noise = 0.0
    b.accounts["Sim102"] = 30_000.0
    await container.accounts.sync_once()
    await container.accounts.set_peak("Sim102", None)
    b.accounts["Sim102"] = 29_000.0
    await container.accounts.sync_once()
    assert container.accounts.accounts["Sim102"].drawdown.peak == 30_000
    # el máximo está guardado: un AccountService nuevo sobre el mismo store lo recupera
    from tradepilot.services.account_service import AccountService
    fresh = AccountService(b, container.bus, container.store)
    await fresh.sync_once()
    assert fresh.accounts["Sim102"].drawdown.peak == 30_000 and fresh.accounts["Sim102"].drawdown.drawdown == 1000
    # reiniciar al valor actual (evaluación nueva) y fijar a mano (el prop firm tiene otro máximo)
    r = await client.put("/api/accounts/Sim102/peak", json={"peak": None})
    assert r.status_code == 200 and r.json()["drawdown"]["peak"] == 29_000 and r.json()["drawdown"]["drawdown"] == 0
    r = await client.put("/api/accounts/Sim102/peak", json={"peak": 31_500})
    assert r.json()["drawdown"]["peak"] == 31_500 and r.json()["drawdown"]["drawdown"] == 2500
    assert any(a.event_type == "PEAK_SET" for a in container.audit.recent(3))
    assert (await client.put("/api/accounts/Nope/peak", json={"peak": None})).status_code == 404
    # tras sincronizar, el máximo fijado a mano se conserva (la cuenta vale menos)
    await container.accounts.sync_once()
    assert container.accounts.accounts["Sim102"].drawdown.peak == 31_500
