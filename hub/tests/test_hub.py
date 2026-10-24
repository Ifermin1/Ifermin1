"""Hub: ingesta idempotente, resumen y autenticación."""
from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient

from hub.app import create_app


@pytest.fixture
async def client():
    app = create_app(db_path=":memory:", token="hub-token")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://hub", headers={"Authorization": "Bearer hub-token"}) as ac:
        yield ac


def _body(node="PC-1", trade_id=1, pnl=120.0):
    today = date.today().isoformat()
    return {"node": {"id": node, "mode": "ninja", "version": "2.9"}, "items": [
        {"kind": "trade", "ref": trade_id, "at": f"{today}T10:00:00", "data": {"id": trade_id, "account_id": "APEX-1", "day": today, "symbol": "NQ 12-26", "side": "long", "quantity": 2, "entry_price": 1, "exit_price": 2, "pnl": pnl, "commissions": 9.0, "opened_at": f"{today}T09:59:00", "closed_at": f"{today}T10:00:00"}},
        {"kind": "snapshot", "at": f"{today}T10:00:05", "data": {"node": node, "day_total": 111.0, "kill_switch": False, "connected": True,
                                                                   "accounts": [{"account_id": "APEX-1", "alias": "Apex 50K", "enabled": True, "net_pnl": 111.0, "balance": 50111.0, "positions": [{"symbol": "NQ", "quantity": 1}], "drawdown": 500.0, "drawdown_limit": 2500.0}]}},
        {"kind": "event", "ref": 77, "at": f"{today}T10:00:06", "data": {"event_type": "DAILY_LOSS_WARNING", "account": "APEX-1", "message": "cerca del límite", "timestamp": f"{today}T10:00:06"}},
        {"kind": "raro", "data": {}},
    ]}


async def test_auth_required(client):
    app = create_app(db_path=":memory:", token="hub-token")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://hub") as anon:
        assert (await anon.post("/api/ingest", json=_body())).status_code == 401
        assert (await anon.get("/api/summary?token=hub-token")).status_code == 200
        assert (await anon.get("/")).status_code == 200 and "Hub" in (await anon.get("/")).text
    app2 = create_app(db_path=":memory:", token="")
    async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://hub", headers={"Authorization": "Bearer x"}) as c2:
        assert (await c2.get("/api/summary")).status_code == 503


async def test_ingest_is_idempotent_and_summary_aggregates(client):
    r = await client.post("/api/ingest", json=_body())
    assert r.status_code == 200 and r.json()["ok"] and r.json()["counts"] == {"trade": 1, "snapshot": 1, "event": 1, "ignored": 1}
    await client.post("/api/ingest", json=_body())                       # reenvío: no duplica
    await client.post("/api/ingest", json=_body(node="PC-2", trade_id=1, pnl=-50.0))
    s = (await client.get("/api/summary")).json()
    assert [n["id"] for n in s["nodes"]] == ["PC-1", "PC-2"]
    n1 = s["nodes"][0]
    assert n1["day_total"] == 111.0 and n1["accounts"][0]["alias"] == "Apex 50K" and n1["accounts"][0]["positions"] == 1 and n1["stale"] is False
    assert n1["today"] == {"n": 1, "net": 111.0, "wins": 1, "win_rate": 1.0}
    assert s["today"]["n"] == 2 and s["today"]["net"] == 52.0 and s["day_total"] == 222.0
    assert len(s["events"]) == 2 and s["events"][0]["event_type"] == "DAILY_LOSS_WARNING"
    assert len((await client.get("/api/trades?days=1")).json()) == 2 and len((await client.get("/api/trades?node=PC-2")).json()) == 1
    assert (await client.post("/api/ingest", json={"node": {}, "items": []})).status_code == 400
