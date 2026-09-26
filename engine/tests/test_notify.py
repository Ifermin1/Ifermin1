import asyncio

from tradepilot.services.notify_service import NotifyConfig


class FakeTelegram:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok
        self.updates = []

    async def __call__(self, token, method, payload):
        self.calls.append((token, method, payload))
        if method == "getUpdates":
            return {"ok": True, "result": self.updates}
        return {"ok": self.ok, "description": None if self.ok else "chat not found"}


async def test_important_events_are_sent_grouped_and_deduplicated(container):
    n = container.notify
    tg = FakeTelegram()
    n.sender = tg
    n.coalesce_s = 0.02
    n.set_config(NotifyConfig(enabled=True, bot_token="123:abc", chat_id="42"))
    container.audit.log("DAILY_LOSS_LIMIT", "Sim102: pérdida diaria -520 <= -500", target="Sim102")
    container.audit.log("FOLLOWER_FILL", "Sim102: BUY 1 NQ", target="Sim102")          # no está en la lista por defecto
    container.audit.log("DESYNC", "Sim103 no coincide con la maestra", target="Sim103")
    container.audit.log("DESYNC", "Sim103 no coincide con la maestra", target="Sim103")  # repetido: se suprime
    await asyncio.sleep(0.1)
    assert len(tg.calls) == 1
    token, method, payload = tg.calls[0]
    assert token == "123:abc" and method == "sendMessage" and payload["chat_id"] == "42" and payload["parse_mode"] == "HTML"
    assert "Límite de pérdida diaria" in payload["text"] and "Sim102" in payload["text"] and "Seguidora desincronizada" in payload["text"]
    assert payload["text"].count("desincronizada") == 1 and "Fill" not in payload["text"]
    assert "&lt;= -500" in payload["text"]                     # HTML escapado
    assert n.stats["sent"] == 1 and n.stats["suppressed"] == 1
    # desactivado: no manda nada
    n.set_config(NotifyConfig(enabled=False, bot_token="123:abc", chat_id="42"))
    container.audit.log("KILL_SWITCH_ON", "Kill switch")
    await asyncio.sleep(0.05)
    assert len(tg.calls) == 1


async def test_errors_are_counted_and_test_and_discover_work(container):
    n = container.notify
    tg = FakeTelegram(ok=False)
    n.sender = tg
    n.set_config(NotifyConfig(enabled=True, bot_token="t", chat_id="1"))
    r = await n.test()
    assert r["ok"] is False and "chat not found" in r["error"] and n.stats["errors"] == 1
    tg.ok = True
    assert (await n.test())["ok"] is True
    assert (await n.discover_chat())["ok"] is False
    tg.updates = [{"message": {"chat": {"id": 987654, "first_name": "Ibrahim", "last_name": "N"}}}]
    r = await n.discover_chat("t")
    assert r == {"ok": True, "chat_id": "987654", "name": "Ibrahim N"}
    assert (await n.discover_chat(""))["ok"] is True          # sin token explícito usa el guardado


async def test_notifications_api(client, container):
    tg = FakeTelegram()
    container.notify.sender = tg
    r = await client.get("/api/notifications")
    assert r.status_code == 200 and r.json()["enabled"] is False and "DAILY_LOSS_LIMIT" in r.json()["defaults"]
    r = await client.put("/api/notifications", json={"enabled": True, "bot_token": " 9:x ", "chat_id": "5", "events": ["desync", "ERROR"]})
    assert r.status_code == 200 and r.json()["bot_token"] == "9:x" and r.json()["events"] == ["DESYNC", "ERROR"]
    assert container.store.get_kv("notifications") is not None
    r = await client.post("/api/notifications/test")
    assert r.status_code == 200 and r.json()["ok"] is True and tg.calls[-1][1] == "sendMessage"
    r = await client.post("/api/notifications/discover-chat", json={})
    assert r.status_code == 200 and r.json()["ok"] is False
