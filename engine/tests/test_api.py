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
