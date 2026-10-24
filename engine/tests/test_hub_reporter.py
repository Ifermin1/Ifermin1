"""Reporter del hub: cola local, reintentos y contenido de lo que se envía."""
import pytest

from tradepilot.container import build_container
from tradepilot.core.config import Settings
from tradepilot.core.events import EventBus
from tradepilot.infrastructure.brokers.mock import MockBridge


class FakeHub:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def __call__(self, url, token, body):
        self.calls.append((url, token, body))
        if self.fail:
            raise ConnectionError("hub caído")
        return {"ok": True, "accepted": len(body["items"])}


@pytest.fixture
def hub_container():
    cfg = Settings(ENGINE_MODE="mock", API_TOKEN="t", DB_PATH=":memory:", WEB_DIST="/nonexistent", HUB_URL="http://hub:8100/", HUB_TOKEN="hub-token", HUB_NODE="PC-test", HUB_INTERVAL_S=5)
    bus = EventBus()
    return build_container(cfg, bridge=MockBridge(bus, emit_events=False))


async def test_reporter_queues_sends_and_retries(hub_container):
    c = hub_container
    hub = c.hub
    assert hub.enabled and hub.node == "PC-test" and hub.url == "http://hub:8100"
    fake = FakeHub()
    hub.sender = fake
    await c.accounts.sync_once()
    c.store.save_trade({"account_id": "Sim101", "day": "2026-10-20", "symbol": "NQ 12-26", "side": "long", "quantity": 1, "entry_price": 1, "exit_price": 2, "pnl": 100.0, "commissions": 4.5, "opened_at": None, "closed_at": "2026-10-20T10:00:00", "fills": 2})
    c.audit.log("KILL_SWITCH_ON", "Kill switch ACTIVADO: prueba")
    c.audit.log("FOLLOWER_FILL", "Sim102: BUY 1 NQ", target="Sim102")       # no es importante: no se manda
    import asyncio
    await asyncio.sleep(0.05)
    # el hub falla: todo queda en cola con el intento anotado
    fake.fail = True
    r = await hub.tick()
    assert r["ok"] is False and "hub caído" in r["error"]
    st = hub.state()
    assert st["queued"] == 3 and st["stats"]["errors"] == 1 and st["last_trade_id"] >= 1       # trade + evento + foto
    assert all(x["attempts"] == 1 for x in c.store.outbox_peek())
    # vuelve: se envía todo en un lote, la cola queda vacía y la foto no se duplica
    fake.fail = False
    r = await hub.tick()
    assert r["ok"] is True and r["sent"] == 3 and hub.state()["queued"] == 0
    url, token, body = fake.calls[-1]
    assert url == "http://hub:8100/api/ingest" and token == "hub-token" and body["node"]["id"] == "PC-test" and body["node"]["mode"] == "mock"
    kinds = sorted(i["kind"] for i in body["items"])
    assert kinds == ["event", "snapshot", "trade"]
    trade = next(i for i in body["items"] if i["kind"] == "trade")
    assert trade["data"]["account_id"] == "Sim101" and trade["data"]["pnl"] == 100.0 and trade["ref"] == trade["data"]["id"]
    snap = next(i for i in body["items"] if i["kind"] == "snapshot")["data"]
    assert snap["node"] == "PC-test" and any(a["account_id"] == "Sim101" for a in snap["accounts"]) and "day_total" in snap and snap["kill_switch"] is False
    ev = next(i for i in body["items"] if i["kind"] == "event")["data"]
    assert ev["event_type"] == "KILL_SWITCH_ON"
    # el siguiente ciclo solo manda la foto (no hay operaciones nuevas)
    r = await hub.tick()
    assert r["sent"] == 1 and fake.calls[-1][2]["items"][0]["kind"] == "snapshot"


async def test_reporter_disabled_without_config(container, client):
    assert container.hub is not None and not container.hub.enabled
    r = await client.get("/api/hub")
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["queued"] == 0
    assert (await client.post("/api/hub/flush")).status_code == 400
    container.audit.log("KILL_SWITCH_ON", "x")
    assert container.store.outbox_count() == 0          # sin hub no se acumula nada
