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
