"""IA 3: asistente por chat (reglas sin clave, IA con clave), planes con confirmación, Telegram y API."""
import asyncio

from tradepilot.services.ai_service import AIService
from tradepilot.services.notify_service import NotifyConfig

ORDER = {"client_id": "as-1", "account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 4, "order_type": "MARKET", "sl_ticks": 0, "tp_ticks": 0}


async def _open(container):
    await container.accounts.sync_once()
    await container.trade.place(dict(ORDER))
    await container.accounts.sync_once()


async def test_rules_answer_queries_and_build_plans(container):
    a = container.assistant
    await _open(container)
    r = await a.handle("¿cuánto llevo hoy?")
    assert r["plan"] is None and "Hoy" in r["reply"] and r["source"] == "regla"
    r = await a.handle("posiciones")
    assert "Sim101 L4 NQ 12-26" in r["reply"]
    r = await a.handle("cierra la mitad de NQ en Sim101")
    assert r["plan"] and len(r["plan"]["actions"]) == 1 and r["plan"]["actions"][0]["kind"] == "close" and r["plan"]["actions"][0]["quantity"] == 2
    res = await a.confirm(r["plan"]["id"])
    assert res["ok"] == 1 and res["failed"] == 0
    await container.accounts.sync_once()
    assert next(p.quantity for p in container.accounts.find("Sim101").open_positions if p.quantity) == 2
    # breakeven en todas, pausa y reanudar
    pos = next(p for p in container.accounts.find("Sim101").open_positions if p.quantity)
    container.market.on_tick("NQ 12-26", pos.avg_price + 10)
    r = await a.handle("pon todos los stops a breakeven")
    assert r["plan"]["actions"][0]["kind"] == "breakeven"
    res = await a.confirm(r["plan"]["id"])
    assert res["ok"] == 1, res
    r = await a.handle("pausa la copia")
    assert r["plan"]["actions"][0]["kind"] == "pause"
    await a.confirm(r["plan"]["id"])
    assert container.risk.kill_switch
    r = await a.handle("reanuda la copia")
    await a.confirm(r["plan"]["id"])
    assert not container.risk.kill_switch
    # plan caducado / cancelado
    r = await a.handle("cierra todo")
    assert r["plan"]["actions"][0]["kind"] == "flatten_all"
    assert a.cancel(r["plan"]["id"]) is True
    try:
        await a.confirm(r["plan"]["id"])
        assert False
    except ValueError:
        pass
    assert any(x.event_type == "ASSISTANT_RUN" for x in container.audit.recent(20))


async def test_ai_actions_are_resolved_against_real_state(container):
    class FakeAI(AIService):
        def __init__(self):
            super().__init__(api_key="k")
        async def ask(self, system, prompt, model=None, max_tokens=None):
            return {"reply": "Cierro la mitad de las 50K y pongo breakeven en el resto.", "actions": [{"kind": "close_half", "tag": "50K"}, {"kind": "breakeven", "account": "Sim999"}], "model": "fake"}
    container.assistant.ai = FakeAI()
    await _open(container)
    await container.accounts.set_settings("Sim101", tags=["50K"])
    r = await container.assistant.handle("cierra la mitad de las 50K")
    assert r["source"] == "ia" and len(r["plan"]["actions"]) == 1 and r["plan"]["actions"][0]["quantity"] == 2 and r["plan"]["actions"][0]["account"] == "Sim101"


async def test_assistant_via_telegram_and_api(client, container):
    await _open(container)
    n = container.notify
    calls = []
    async def bot(token, method, payload):
        calls.append((method, payload)); return {"ok": True, "result": {"message_id": 1}}
    n.sender = bot
    n.set_config(NotifyConfig(enabled=False, bot_token="t", chat_id="42"))
    tc = n.commands
    await tc.handle({"update_id": 1, "message": {"chat": {"id": 42}, "text": "cierra la mitad de NQ en Sim101"}})
    kb = next(p["reply_markup"] for m, p in calls if m == "sendMessage" and "reply_markup" in p)
    data = kb["inline_keyboard"][0][0]["callback_data"]
    assert data.startswith("assist:")
    await tc.handle({"update_id": 2, "callback_query": {"id": "c", "data": data, "from": {"id": 42}, "message": {"message_id": 1, "chat": {"id": 42}}}})
    texts = [p.get("text", "") for m, p in calls if m == "sendMessage"]
    assert any("✅ cerrar 2 de 4" in t for t in texts), texts
    r = await client.post("/api/assistant", json={"text": "¿cuánto llevo hoy?"})
    assert r.status_code == 200 and r.json()["plan"] is None
    r = await client.post("/api/assistant", json={"text": "cierra todo"})
    pid = r.json()["plan"]["id"]
    assert (await client.post("/api/assistant/cancel", json={"id": pid})).json()["cancelled"] is True
    assert (await client.post("/api/assistant/confirm", json={"id": pid})).status_code == 410
