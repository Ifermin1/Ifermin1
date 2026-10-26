"""Token de solo lectura: ve todo, no puede operar ni cambiar nada."""
import pytest
from httpx import ASGITransport, AsyncClient

from tradepilot.api.app import create_app
from tradepilot.api.auth import reset_throttle
from tradepilot.container import build_container
from tradepilot.core.config import Settings
from tradepilot.core.events import EventBus
from tradepilot.infrastructure.brokers.mock import MockBridge


@pytest.fixture
async def pair():
    reset_throttle()
    cfg = Settings(ENGINE_MODE="mock", API_TOKEN="full-token", API_READ_TOKEN="read-token", DB_PATH=":memory:", WEB_DIST="/nonexistent")
    c = build_container(cfg, bridge=MockBridge(EventBus(), emit_events=False))
    app = create_app(c)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": "Bearer read-token"}) as ro, \
                   AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": "Bearer full-token"}) as rw:
            yield ro, rw, c


async def test_read_token_sees_everything_but_cannot_act(pair):
    ro, rw, c = pair
    h = (await ro.get("/api/health")).json()
    assert h["role"] == "read" and h["read_token_set"] is True
    assert (await rw.get("/api/health")).json()["role"] == "full"
    for path in ("/api/accounts", "/api/risk", "/api/audit?limit=5", "/api/rules", "/api/trade/status", "/api/push", "/api/hub"):
        assert (await ro.get(path)).status_code == 200, path
    # consultas con cuerpo permitidas
    assert (await ro.post("/api/ai/review", json={"day": "", "force": False})).status_code == 200
    assert (await ro.post("/api/ai/check-entry", json={"account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 1, "order_type": "MARKET", "sl_ticks": 0, "tp_ticks": 0, "ai": False})).status_code in (200, 400)
    # todo lo que cambia algo: 403, y no deja rastro
    before = c.risk.kill_switch
    for method, path, body in (("post", "/api/risk/kill-switch", {"active": True, "reason": "x"}),
                               ("post", "/api/trade/order", {"client_id": "r1", "account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 1, "order_type": "MARKET", "sl_ticks": 0, "tp_ticks": 0}),
                               ("post", "/api/risk/flatten-all", {"include_master": True}),
                               ("put", "/api/notifications", {"enabled": False, "bot_token": "", "chat_id": "", "events": []}),
                               ("patch", "/api/accounts/Sim101", {"enabled": False}),
                               ("delete", "/api/rules/xyz", None)):
        r = await getattr(ro, method)(path, json=body) if body is not None else await getattr(ro, method)(path)
        assert r.status_code == 403 and "solo lectura" in r.text, (path, r.status_code, r.text)
    assert c.risk.kill_switch == before
    # el token completo sigue pudiendo
    assert (await rw.post("/api/risk/kill-switch", json={"active": True, "reason": "test"})).status_code == 200
    # un token inválido sigue siendo 401
    async with AsyncClient(transport=ASGITransport(app=ro._transport.app), base_url="http://test", headers={"Authorization": "Bearer nope"}) as bad:
        assert (await bad.get("/api/health")).status_code == 401
