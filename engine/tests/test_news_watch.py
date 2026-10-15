import json
from datetime import datetime, timedelta

from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.services.ai_service import AIService, heuristic_post, parse_json, surprise_of, to_number
from tradepilot.services.news_service import NewsService


class FakeAudit:
    def __init__(self):
        self.events = []

    def log(self, event_type, message, source=None, target=None, details=None):
        self.events.append({"event_type": event_type, "message": message, "details": details or {}})


def ev(title, when, impact="High", forecast="150K", previous="142K", actual="", country="USD"):
    return {"title": title, "country": country, "date": when.isoformat(), "impact": impact, "forecast": forecast, "previous": previous, "actual": actual}


def test_numbers_and_surprise():
    assert to_number("150K") == 150000 and to_number("3.2%") == 3.2 and to_number("-1.2M") == -1200000 and to_number("abc") is None and to_number(None) is None
    sp = surprise_of({"actual": "3.4%", "forecast": "3.1%"})
    assert sp["tag"] == "mayor" and round(sp["diff"], 2) == 0.3
    assert surprise_of({"actual": "150K", "forecast": "151K"})["tag"] == "en línea"
    assert surprise_of({"actual": "x", "forecast": "1"}) is None
    assert parse_json('bla {"summary": "hola", "bias": "alcista"} fin')["bias"] == "alcista"
    assert parse_json("texto plano")["summary"] == "texto plano"
    h = heuristic_post({"title": "IPC", "actual": "3.4%", "forecast": "3.1%", "previous": "3.0%"}, {"symbol": "ES", "px_before": 6000, "px_15m": 5982})
    assert h["surprise"] == "mayor" and h["bias"] == "bajista" and "ES -0.30 %" in h["move"]


async def test_second_feed_failure_is_not_an_error_and_cache_is_per_feed():
    t = datetime(2026, 9, 22, 9, 0).astimezone()
    state = {"next_ok": True, "this_ok": True}

    async def fetcher(url):
        if "nextweek" in url:
            if not state["next_ok"]:
                raise RuntimeError("404 Not Found")
            return [ev("Next week thing", t + timedelta(days=8))]
        if not state["this_ok"]:
            raise RuntimeError("timeout")
        return [ev("NFP", t + timedelta(hours=2))]
    n = NewsService(fetcher=fetcher, ttl_s=0)
    n.now = lambda: t
    r = await n.get(days=14)
    assert [e["title"] for e in r["events"]] == ["NFP", "Next week thing"] and r["error"] is None
    state["next_ok"] = False
    r = await n.get(days=14)
    assert r["error"] is None and len(r["events"]) == 2            # la semana siguiente falla: se conserva y no hay aviso
    state["this_ok"] = False
    r = await n.get(days=14)
    assert "timeout" in r["error"] and len(r["events"]) == 2       # la semana en curso falla: aviso, pero se sirve lo último
    assert r["refresh_s"] == 0 and r["ai"]["enabled"] is False and r["events"][0]["watched"] is True


async def test_watch_alerts_results_reaction_and_stats(tmp_path):
    store = SQLiteStore(str(tmp_path / "n.db"))
    t = datetime(2026, 9, 22, 9, 0).astimezone()
    feed = [ev("Nóminas no agrícolas", t + timedelta(minutes=20)),
            ev("IPC (interanual)", t - timedelta(minutes=20), forecast="3.1%", previous="3.0%", actual="3.4%"),
            ev("Lejana", t + timedelta(hours=3), impact="Medium"),
            ev("BCE", t + timedelta(minutes=5), country="EUR")]           # no vigilado (solo USD)

    async def fetcher(url):
        return feed if "thisweek" in url else []
    audit = FakeAudit()
    n = NewsService(fetcher=fetcher, ttl_s=0, store=store, audit=audit, alert_minutes=30)
    n.now = lambda: t
    # ticks de ES: 6000 antes del IPC, cae 20 puntos al salir y rebota un poco
    t0 = (t - timedelta(minutes=20)).timestamp()
    for s in range(-600, 1201, 30):
        n.on_tick("ES 12-26", 6000 - (20 if s > 0 else 0) + (3 if s > 600 else 0), t0 + s)
    await n.tick()
    kinds = [a["event_type"] for a in audit.events]
    assert sorted(kinds) == ["NEWS_RESULT", "NEWS_SOON"]
    res, soon = audit.events
    assert "Nóminas" in soon["message"] and "en 20 min" in soon["message"] and soon["details"]["source"] == "regla" and "ANTHROPIC_API_KEY" in soon["message"]
    assert "real 3.4%" in res["message"] and "dato mayor" in res["message"] and res["details"]["reaction"]["px_before"] == 6000 and res["details"]["reaction"]["px_5m"] == 5980
    assert res["details"]["reaction"]["px_15m"] == 5983 and res["details"]["analysis"]["bias"] == "bajista"
    await n.tick()
    assert len(audit.events) == 2                                      # no se repite
    al = n.alerts()
    assert [u["event"]["title"] for u in al["upcoming"]] == ["Nóminas no agrícolas"] and al["upcoming"][0]["analysis"]["source"] == "regla"
    assert al["next"]["event"]["title"] == "Lejana" and al["results"][0]["event"]["title"] == "IPC (interanual)" and al["results"][0]["surprise"]["tag"] == "mayor"
    # persistencia: un servicio nuevo sobre la misma BD recuerda análisis y avisos
    n2 = NewsService(fetcher=fetcher, ttl_s=0, store=store, audit=audit, alert_minutes=30)
    n2.now = lambda: t
    await n2.tick()
    assert len(audit.events) == 2
    assert set(k[1] for k in n2._analyses) == {"pre", "post"}
    assert json.loads(store.get_kv("news.alerted"))[-1].endswith("-USD-nóminasnoagrícolas")
    # métricas: una operación durante el IPC y otra fuera
    day = t.strftime("%Y-%m-%d")
    store.save_trade({"account_id": "A", "day": day, "symbol": "ES 12-26", "side": "long", "quantity": 1, "entry_price": 6000, "exit_price": 5990, "pnl": -500, "commissions": 4,
                      "opened_at": (t - timedelta(minutes=25)).isoformat(), "closed_at": (t - timedelta(minutes=15)).isoformat(), "fills": 2})
    store.save_trade({"account_id": "A", "day": day, "symbol": "ES 12-26", "side": "long", "quantity": 1, "entry_price": 6000, "exit_price": 6010, "pnl": 500, "commissions": 4,
                      "opened_at": (t - timedelta(hours=3)).isoformat(), "closed_at": (t - timedelta(hours=2)).isoformat(), "fills": 2})
    st = n2.stats(30)
    assert st["trades"]["near_high"]["n"] == 1 and st["trades"]["near_high"]["net"] == -504 and st["trades"]["outside"]["n"] == 1 and st["trades"]["outside"]["win_rate"] == 1
    assert st["reactions"]["high"]["n"] == 1 and st["reactions"]["high"]["avg_15m"] == round(17 / 6000 * 100, 3)
    assert st["surprises"]["total"] == {"n": 1, "mayor": 1, "menor": 0, "en línea": 0} and st["follows_surprise"] == {"agree": 0, "disagree": 1}
    store.close()


async def test_ai_analysis_is_used_and_cached(tmp_path):
    store = SQLiteStore(str(tmp_path / "ai.db"))
    t = datetime(2026, 9, 22, 9, 0).astimezone()
    calls = []

    class FakeAI(AIService):
        def __init__(self):
            super().__init__(api_key="sk-test", model="claude-test")

        async def ask(self, system, prompt):
            calls.append(prompt)
            if len(calls) == 1:
                raise RuntimeError("límite de peticiones")
            return {"summary": "Dato clave para la Fed.", "bias": "volátil", "move": "0,5 %", "advice": "No entres 5 min antes.", "confidence": 0.7, "model": "claude-test"}

    async def fetcher(url):
        return [ev("NFP", t + timedelta(minutes=10))] if "thisweek" in url else []
    audit = FakeAudit()
    n = NewsService(fetcher=fetcher, ttl_s=0, store=store, ai=FakeAI(), audit=audit)
    n.now = lambda: t
    await n.tick()
    assert audit.events[0]["details"]["source"] == "regla"            # la IA falló: aviso con reglas, se reintenta después
    e = n.find(audit.events[0]["details"]["event_id"])
    a = await n.ensure_analysis(e, "pre")
    assert a["source"] == "ia" and a["model"] == "claude-test" and a["data"]["advice"] == "No entres 5 min antes."
    assert (await n.ensure_analysis(e, "pre"))["source"] == "ia" and len(calls) == 2      # cacheado
    assert store.get_news_analyses([e["id"]])[0]["data"]["bias"] == "volátil"
    store.close()


async def test_news_endpoints(client, container):
    container.news.seed_demo()
    r = await client.get("/api/news/alerts")
    assert r.status_code == 200
    body = r.json()
    assert [u["event"]["title"] for u in body["upcoming"]] == ["Nóminas no agrícolas"] and body["results"][0]["event"]["actual"] == "3.4%"
    assert body["results"][0]["reaction"]["symbol"] == "ES" and body["ai"]["enabled"] is False and body["window_min"] == 30
    eid = body["upcoming"][0]["event"]["id"]
    r = await client.post(f"/api/news/analyze/{eid}?kind=pre")
    assert r.status_code == 200 and r.json()["analysis"]["source"] == "regla" and r.json()["analysis"]["data"]["bias"] == "volátil"
    assert (await client.post(f"/api/news/analyze/{eid}?kind=post")).status_code == 409
    assert (await client.post("/api/news/analyze/nope?kind=pre")).status_code == 404
    r = await client.get("/api/news/stats?days=30")
    assert r.status_code == 200 and set(r.json()["trades"]) == {"near_high", "near_medium", "outside"}
    r = await client.get("/api/news?days=7&countries=USD")
    assert r.status_code == 200 and r.json()["analyses"][eid]["pre"]["source"] == "regla" and r.json()["watch"]["alert_minutes"] == 30
