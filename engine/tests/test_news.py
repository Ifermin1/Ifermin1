from datetime import datetime, timedelta

from tradepilot.services.news_service import NewsService

SAMPLE = [
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-09-25T08:30:00-04:00", "impact": "High", "forecast": "150K", "previous": "142K"},
    {"title": "ECB Press Conference", "country": "EUR", "date": "2026-09-24T08:45:00-04:00", "impact": "High", "forecast": "", "previous": ""},
    {"title": "Crude Oil Inventories", "country": "USD", "date": "2026-09-23T10:30:00-04:00", "impact": "Low", "forecast": "-1.2M", "previous": "0.4M"},
    {"title": "Bank Holiday", "country": "JPY", "date": "2026-09-23T00:00:00-04:00", "impact": "Holiday"},
    {"title": "", "country": "USD", "date": "2026-09-23T10:30:00-04:00"},                 # sin título: se ignora
    {"title": "Old", "country": "USD", "date": "2026-09-01T10:30:00-04:00", "impact": "High"},   # fuera de ventana
]


async def test_news_parses_filters_and_caches():
    calls = []

    async def fetcher(url):
        calls.append(url)
        return SAMPLE if "thisweek" in url else []
    n = NewsService(fetcher=fetcher)
    n.now = lambda: datetime(2026, 9, 22, 9, 0)
    r = await n.get(days=7)
    assert [e["title"] for e in r["events"]] == ["Bank Holiday", "Crude Oil Inventories", "ECB Press Conference", "Non-Farm Employment Change"]
    assert r["events"][3]["impact"] == "high" and r["events"][3]["forecast"] == "150K" and r["events"][3]["country_name"] == "EE. UU."
    assert r["error"] is None and r["fetched_at"] is not None and r["countries"] == ["EUR", "JPY", "USD"]
    assert len(calls) == 2
    usd = await n.get(days=7, countries=["usd"], min_impact="medium")
    assert [e["title"] for e in usd["events"]] == ["Non-Farm Employment Change"]
    assert len(calls) == 2                                   # cacheado
    n.ttl_s = 0
    await n.get()
    assert len(calls) == 4                                   # caducado: se vuelve a descargar


async def test_news_keeps_last_data_when_the_source_fails():
    state = {"ok": True}

    async def fetcher(url):
        if not state["ok"]:
            raise RuntimeError("timeout")
        return SAMPLE
    n = NewsService(fetcher=fetcher, ttl_s=0)
    n.now = lambda: datetime(2026, 9, 22, 9, 0)
    assert len((await n.get())["events"]) == 4
    state["ok"] = False
    r = await n.get()
    assert len(r["events"]) == 4 and "timeout" in r["error"]


async def test_news_api_and_demo(client, container):
    container.news.seed_demo()
    r = await client.get("/api/news?days=7&countries=USD&min_impact=medium")
    assert r.status_code == 200
    body = r.json()
    assert body["events"] and all(e["country"] == "USD" and e["impact"] in ("high", "medium") for e in body["events"])
    assert datetime.fromisoformat(body["events"][0]["time"]) > datetime.now().astimezone() - timedelta(days=8)
