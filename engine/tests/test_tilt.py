"""IA 5: detector de tilt."""
import asyncio


def _t(acc, i, pnl, qty=1, gap_s=300, minute=0):
    o = 9 * 3600 + i * 600
    c = o + 120
    return {"account_id": acc, "day": "2026-10-27", "symbol": "NQ 12-26", "side": "long", "quantity": qty, "entry_price": 1, "exit_price": 2, "pnl": pnl, "commissions": 0,
            "opened_at": f"2026-10-27T{o // 3600:02d}:{(o % 3600) // 60:02d}:{o % 60:02d}", "closed_at": f"2026-10-27T{c // 3600:02d}:{(c % 3600) // 60:02d}:{c % 60:02d}", "fills": 2}


def test_signals(container):
    t = container.tilt
    assert t.signals_for("A", [_t("A", i, 50) for i in range(4)]) == []
    sig = t.signals_for("A", [_t("A", 0, 100), _t("A", 1, -50), _t("A", 2, -60, qty=2), _t("A", 3, -70, qty=3)])
    kinds = {s["kind"] for s in sig}
    assert "streak" in kinds and "size_up" in kinds
    # reentradas rápidas: abre 20 s después de cerrar perdiendo, dos veces
    trades = [_t("A", 0, -30), _t("A", 1, -30), _t("A", 2, -30)]
    trades[1]["opened_at"], trades[1]["closed_at"] = "2026-10-27T09:02:20", "2026-10-27T09:03:00"
    trades[2]["opened_at"], trades[2]["closed_at"] = "2026-10-27T09:03:20", "2026-10-27T09:04:00"
    assert "reentry" in {s["kind"] for s in t.signals_for("A", trades)}
    # hundimiento: iba +600 y acaba en −600
    sig = t.signals_for("A", [_t("A", 0, 600), _t("A", 1, -400), _t("A", 2, -400), _t("A", 3, -400)])
    assert "day_drop" in {s["kind"] for s in sig}


async def test_on_trade_audits_and_auto_pauses(container):
    t = container.tilt
    t.set_config(auto_pause_min=15)
    for i, pnl in enumerate((-50, -60, -70)):
        container.store.save_trade(_t("Sim102", i, pnl, qty=i + 1))
    r = await t.on_trade({"account_id": "Sim102", "day": "2026-10-27"})
    assert r and r["score"] >= 4
    await asyncio.sleep(0.02)
    ev = [a for a in container.audit.recent(10) if a.event_type == "TILT"]
    assert ev and "Sim102" in ev[0].message and "pausada 15 min" in ev[0].message
    lim = container.risk.limits["Sim102"]
    assert lim.trading_halted and lim.halted_reason == "tilt" and "Sim102" in t.paused
    assert not container.risk.allows("Sim102", 1, "NQ 12-26", "BUY")[0]
    # no repite el aviso en 15 min; la pausa caduca y se reanuda sola
    assert await t.on_trade({"account_id": "Sim102", "day": "2026-10-27"}) is None
    t.paused["Sim102"] = 0
    t.expire_pauses()
    assert not container.risk.limits["Sim102"].trading_halted and any(a.event_type == "TILT_RESUME" for a in container.audit.recent(10))


async def test_tilt_api_and_hook_from_performance(client, container):
    r = await client.get("/api/tilt")
    assert r.status_code == 200 and r.json()["config"]["streak"] == 3
    r = await client.put("/api/tilt", json={"streak": 2, "auto_pause_min": 5})
    assert r.json()["config"]["streak"] == 2 and r.json()["config"]["auto_pause_min"] == 5
    assert (await client.put("/api/tilt", json={"streak": 1})).status_code == 422
    # el servicio de rendimiento avisa al cerrar una operación (gancho on_trade_closed)
    called = []
    async def hook(trade):
        called.append(trade)
    container.performance.on_trade_closed = hook
    from datetime import datetime
    container.performance.note_fill("Sim101", "NQ 12-26", "BUY", 1, 100.0, datetime(2026, 10, 27, 10, 0))
    container.performance.note_fill("Sim101", "NQ 12-26", "SELL", 1, 90.0, datetime(2026, 10, 27, 10, 5))
    await asyncio.sleep(0.02)
    assert called and called[0]["account_id"] == "Sim101" and called[0]["pnl"] < 0
    r = await client.post("/api/tilt/resume", json={"account": "Sim101"})
    assert r.status_code == 200 and r.json()["resumed"] is False
