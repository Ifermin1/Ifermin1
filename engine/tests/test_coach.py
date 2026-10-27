from datetime import datetime

from httpx import AsyncClient

from tradepilot.domain import tradestats as ts
from tradepilot.services.ai_service import AIService


def _trade(acc, day, t0, t1, net, qty=1, side="long", symbol="NQ 12-26"):
    return {"account_id": acc, "day": day, "symbol": symbol, "side": side, "quantity": qty, "entry_price": 1, "exit_price": 1, "pnl": net + 4, "commissions": 4,
            "opened_at": f"{day}T{t0}", "closed_at": f"{day}T{t1}", "fills": 2}


def test_tradestats_basics():
    day = "2026-10-20"
    trades = [_trade("A", day, "09:30:00", "09:30:20", -100), _trade("A", day, "09:35:00", "09:36:00", -50), _trade("A", day, "10:00:00", "10:10:00", 300), _trade("B", day, "10:05:00", "10:05:30", -80)]
    s = ts.trade_stats(trades)
    assert s["n"] == 4 and s["wins"] == 1 and s["net"] == 70 and s["max_loss_streak"] == 3 and s["pf"] == round(300 / 230, 2)   # por orden de cierre: A, A, B pierden antes de la ganadora
    assert [b["key"] for b in ts.by_hour(trades)] == ["09", "10"] and ts.by_account(trades)[0]["key"] == "A"
    assert ts.worst(trades, 1)[0]["account_id"] == "A" and ts.seconds_of(trades[0]) == 20


async def test_review_heuristic_and_cached(client: AsyncClient, container):
    day = datetime.now().strftime("%Y-%m-%d")
    for t in (_trade("Sim101", day, "09:30:00", "09:30:20", -200), _trade("Sim101", day, "09:40:00", "09:40:30", -150), _trade("Sim101", day, "09:50:00", "09:51:00", -120), _trade("Sim101", day, "11:00:00", "11:20:00", 400)):
        container.store.save_trade(t)
    r = await client.post("/api/ai/review", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "regla" and d["stats"]["n"] == 4
    titles = [p["title"] for p in d["review"]["patterns"]]
    assert any("menos de 60 s" in t for t in titles) and any("Racha" in t for t in titles) and len(d["review"]["tomorrow"]) == 3 and 0 <= d["review"]["score"] <= 10
    # segunda llamada: cacheada (misma hora de generación)
    r2 = await client.post("/api/ai/review", json={})
    assert r2.json()["generated_at"] == d["generated_at"]
    assert (await client.post("/api/ai/review", json={"day": "ayer"})).status_code == 400


async def test_review_with_ai_and_ask(client: AsyncClient, container):
    class FakeAI(AIService):
        def __init__(self):
            super().__init__(api_key="sk-test", model="claude-test")
            self.prompts = []

        async def ask(self, system, prompt, model=None, max_tokens=None):
            self.prompts.append((system[:30], prompt))
            if "coach" in system:
                return {"summary": "Día flojo.", "patterns": [{"title": "Entradas tras noticia", "evidence": "x", "cost_usd": -300}], "tomorrow": ["a", "b", "c"], "score": 5, "mood": "regular", "model": "claude-test"}
            if "analista" in system:
                return {"answer": "Los jueves ganas más.", "numbers": ["Jue: +1.200 $"], "model": "claude-test"}
            return {"verdict": "ámbar", "reason": "noticia cerca", "model": "claude-test"}
    fake = FakeAI()
    container.coach.ai = fake
    day = datetime.now().strftime("%Y-%m-%d")
    container.store.save_trade(_trade("Sim101", day, "09:30:00", "09:32:00", -200))
    r = await client.post("/api/ai/review", json={"force": True})
    assert r.status_code == 200 and r.json()["source"] == "ia" and r.json()["review"]["score"] == 5 and "Datos de la sesión" in fake.prompts[-1][1]
    r = await client.post("/api/ai/ask", json={"question": "¿Qué día de la semana gano más?"})
    assert r.status_code == 200 and r.json()["answer"].startswith("Los jueves") and r.json()["context"]["n_trades"] >= 1
    container.coach.ai = AIService()
    assert (await client.post("/api/ai/ask", json={"question": "¿y los martes?"})).status_code == 503


async def test_check_entry_traffic_light(client: AsyncClient, container):
    r = await client.post("/api/ai/check-entry", json={"account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 1, "sl_ticks": 40})
    assert r.status_code == 200
    d = r.json()
    assert d["verdict"] in ("green", "amber") and all(x["level"] != "red" for x in d["reasons"])      # sin límites: ámbar como mucho
    # kill switch → rojo; 3 pérdidas seguidas → rojo; sin stop → ámbar
    await client.post("/api/risk/kill-switch", json={"active": True, "reason": "prueba"})
    d = (await client.post("/api/ai/check-entry", json={"account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 1})).json()
    assert d["verdict"] == "red" and any("kill switch" in x["text"] for x in d["reasons"]) and any("sin stop" in x["text"] for x in d["reasons"])
    await client.post("/api/risk/kill-switch", json={"active": False})
    day = datetime.now().strftime("%Y-%m-%d")
    for i in range(3):
        container.store.save_trade(_trade("Sim102", day, f"09:3{i}:00", f"09:3{i}:30", -50))
    d = (await client.post("/api/ai/check-entry", json={"account": "Sim102", "symbol": "NQ 12-26", "action": "SELL", "quantity": 1, "sl_ticks": 40})).json()
    assert d["verdict"] == "red" and any("3 pérdidas seguidas" in x["text"] for x in d["reasons"])


async def test_check_entry_with_ai_normalizes_verdict_and_adds_context(container):
    from tradepilot.services.ai_service import AIService

    class FakeAI(AIService):
        def __init__(self):
            super().__init__(api_key="k")
            self.prompts = []
        async def ask(self, system, prompt, model=None, max_tokens=None):
            self.prompts.append(prompt)
            return {"verdict": "rojo", "reason": "racha y noticia en 5 min", "suggestion": "espera 15 min", "model": "fake"}
    fake = FakeAI()
    container.coach.ai = fake
    await container.accounts.sync_once()
    r = await container.coach.check_entry({"account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 1, "order_type": "MARKET", "sl_ticks": 40, "tp_ticks": 80}, use_ai=True)
    assert r["ai"]["verdict"] == "red" and r["ai"]["suggestion"] == "espera 15 min" and r["ai"]["model"] == "fake" and "ms" in r["ai"]
    import json
    sent = json.loads(fake.prompts[0])
    assert sent["context"]["account"]["id"] == "Sim101" and "today" in sent["context"] and "market" in sent["context"] and "verdict_rules" in sent
