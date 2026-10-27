"""IA 4: briefing de la mañana y cierre de sesión automáticos."""
from datetime import datetime


async def test_brief_and_review_by_rules_and_schedule(container):
    d = container.digest
    await container.accounts.sync_once()
    r = await d.morning_brief("manual")
    assert r["source"] == "regla" and r["text"].startswith("📅 Briefing") and d.last("brief")["day"] == r["day"]
    assert any(a.event_type == "MORNING_BRIEF" for a in container.audit.recent(10))
    r2 = await d.session_review("manual")
    assert r2["text"].startswith("🏁 Cierre") and d.last("review")["text"] == r2["text"]
    st = d.state()
    assert st["config"]["brief_time"] == "08:00" and st["brief"]["day"] == r["day"] and st["review"]
    # programación: a la hora del briefing se manda una vez; un martes a las 08:00 sí, el sábado no
    container.store.set_kv("digest.brief.2026-10-27", "")
    container.store.set_kv("digest.review.2026-10-27", "")
    d.set_config(brief_time="08:00", review_time="17:05")
    sent = []
    async def fake_brief(reason="x"):
        sent.append(("brief", reason)); return {}
    async def fake_review(reason="x"):
        sent.append(("review", reason)); return {}
    d.morning_brief, d.session_review = fake_brief, fake_review
    d.now = lambda: datetime(2026, 10, 27, 7, 59)          # martes, antes de la hora
    await d.tick()
    assert sent == []
    d.now = lambda: datetime(2026, 10, 27, 8, 1)
    await d.tick()
    assert sent == [("brief", "automático")]
    container.store.set_kv("digest.brief.2026-10-27", "{}")
    await d.tick()
    assert sent == [("brief", "automático")]               # no se repite
    d.now = lambda: datetime(2026, 10, 27, 17, 6)
    await d.tick()
    assert sent[-1] == ("review", "automático")
    d.now = lambda: datetime(2026, 10, 31, 8, 30)          # sábado
    await d.tick()
    assert len(sent) == 2
    d.set_config(enabled=False)
    d.now = lambda: datetime(2026, 10, 28, 8, 30)
    await d.tick()
    assert len(sent) == 2


async def test_digest_api(client, container):
    r = await client.get("/api/digest")
    assert r.status_code == 200 and r.json()["config"]["enabled"] is True
    r = await client.put("/api/digest", json={"brief_time": "07:45", "weekdays_only": False})
    assert r.json()["config"]["brief_time"] == "07:45" and r.json()["config"]["weekdays_only"] is False
    assert (await client.put("/api/digest", json={"brief_time": "7am"})).status_code == 422
    r = await client.post("/api/digest/run", json={"kind": "brief"})
    assert r.status_code == 200 and "Briefing" in r.json()["text"]
