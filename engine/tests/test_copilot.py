"""IA 2: copiloto en vivo (reglas sin clave, IA con clave) y API."""
import asyncio

from tradepilot.services.ai_service import AIService

ORDER = {"client_id": "cp-1", "account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 2, "order_type": "MARKET", "sl_ticks": 0, "tp_ticks": 0}


async def test_rules_propose_protect_then_breakeven(container):
    cp = container.copilot
    await container.accounts.sync_once()
    assert cp.state()["suggestions"] == [] and cp.state()["positions"] == 0
    await container.trade.place(dict(ORDER))
    await container.accounts.sync_once()
    r = await cp.run("manual")
    assert r["source"] == "regla" and r["positions"] == 1
    assert r["suggestions"] and r["suggestions"][0]["action"] == "protect" and r["suggestions"][0]["price"] and r["suggestions"][0]["label"] == "Poner stop"
    await asyncio.sleep(0.05)
    assert any(a.event_type == "COPILOT" for a in container.audit.recent(10))
    # con stop puesto y la posición en +1.5 R, propone breakeven
    pos = next(p for p in container.accounts.find("Sim101").open_positions if p.quantity)
    await container.trade.protect({"client_id": "cp-p", "account": "Sim101", "symbol": "NQ 12-26", "sl_price": pos.avg_price - 20, "last_price": pos.avg_price})
    await container.accounts.sync_once()
    container.market.on_tick("NQ 12-26", pos.avg_price + 30)
    r = await cp.run("manual")
    acts = {s["action"] for s in r["suggestions"]}
    assert "breakeven" in acts or "tighten" in acts, r["suggestions"]
    # el mismo aviso no se repite antes de remind_s
    n = len([a for a in container.audit.recent(20) if a.event_type == "COPILOT"])
    await cp.run("manual")
    assert len([a for a in container.audit.recent(20) if a.event_type == "COPILOT"]) == n


async def test_ai_suggestions_are_filtered_to_known_positions(container):
    class FakeAI(AIService):
        def __init__(self):
            super().__init__(api_key="k")
        async def ask(self, system, prompt, model=None, max_tokens=None):
            return {"items": [{"account": "Sim101", "symbol": "NQ 12-26", "action": "reduce", "severity": "warn", "title": "Reduce antes del dato", "detail": "NFP en 8 min"},
                              {"account": "Sim999", "symbol": "ES 12-26", "action": "close", "severity": "bad", "title": "x", "detail": "y"},
                              {"account": "Sim101", "symbol": "NQ 12-26", "action": "hold", "severity": "info", "title": "nada", "detail": ""}], "model": "fake"}
    container.copilot.ai = FakeAI()
    await container.accounts.sync_once()
    await container.trade.place(dict(ORDER))
    await container.accounts.sync_once()
    r = await container.copilot.run("manual")
    assert r["source"] == "ia" and r["model"] == "fake" and len(r["suggestions"]) == 1
    s = r["suggestions"][0]
    assert s["action"] == "reduce" and s["label"] == "Cerrar la mitad" and s["quantity"] == 2


async def test_copilot_api(client, container):
    r = await client.get("/api/copilot")
    assert r.status_code == 200 and r.json()["enabled"] is True
    r = await client.post("/api/copilot/enabled", json={"enabled": False})
    assert r.json()["enabled"] is False and container.store.get_kv("copilot.enabled") == "0"
    r = await client.post("/api/copilot/run")
    assert r.status_code == 200 and r.json()["reason"] == "manual"
