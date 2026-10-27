"""Robustez 6: métricas por hora, contadores desde la auditoría y salida Prometheus."""
import asyncio
from datetime import datetime, timedelta

from tradepilot.services.metrics_service import MetricsService, _pct, hour_key


def test_percentiles():
    assert _pct([1, 2, 3, 4, 5], 0.5) == 3 and _pct([1, 2, 3, 4, 5], 0.95) == 4.8 and _pct([], 0.5) == 0.0


async def test_observe_counters_rollup_and_summary(container):
    m = container.metrics
    await container.accounts.sync_once()                  # observa sync_ms
    for v in (10, 20, 30, 40, 100):
        m.observe("copy_fill_ms", v)
    container.audit.log("FOLLOWER_FILL", "x", target="Sim102")
    container.audit.log("FOLLOWER_REJECTED", "y", target="Sim102")
    container.audit.log("ADDON_SILENT", "z")
    await asyncio.sleep(0.05)
    cur = m.current()
    assert cur["histograms"]["copy_fill_ms"]["n"] == 5 and cur["histograms"]["copy_fill_ms"]["p50"] == 30 and cur["histograms"]["copy_fill_ms"]["max"] == 100
    assert cur["histograms"]["sync_ms"]["n"] >= 1
    assert cur["counters"] == {"fills": 1, "rejected": 1, "disconnects": 1} and m.totals["fills"] == 1
    m.flush()
    rows = container.store.metrics_range(hour_key())
    assert {r["metric"] for r in rows} >= {"copy_fill_ms", "fills", "rejected", "disconnects"}
    # cambio de hora: la hora anterior queda guardada y la nueva empieza de cero
    past = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:00")
    m.hour = past
    m.samples.clear(); m.counters.clear()
    m.samples["manual_order_ms"].append(7.0)              # una muestra "de la hora anterior"
    m._roll_if_needed()                                   # hour_key() != 2026-10-25T10:00 -> vuelca esa hora y empieza la actual
    assert m.hour == hour_key() and not m.samples
    s = m.summary(24 * 2)
    assert s["gauges"]["accounts_enabled"] >= 1 and "connected" in s["gauges"] and s["totals"]["fills"] == 1
    assert any(h["hour"] == past and "manual_order_ms" in h for h in s["hourly"])
    assert any(d["day"] == past[:10] and d["manual_order_ms_n"] >= 1 for d in s["daily"])


async def test_prometheus_and_api(client, container):
    container.metrics.observe("copy_fill_ms", 42)
    container.metrics.inc("fills", 3)
    r = await client.get("/api/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    txt = r.text
    assert "tradepilot_up 1" in txt and "tradepilot_fills_total 3" in txt and 'tradepilot_copy_fill_ms{quantile="0.95"} 42' in txt and "tradepilot_connected" in txt
    r = await client.get("/api/metrics/summary?hours=24")
    assert r.status_code == 200 and r.json()["current"]["histograms"]["copy_fill_ms"]["n"] == 1


async def test_trade_place_observes_latency(container):
    await container.accounts.sync_once()
    await container.trade.place({"client_id": "m-1", "account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 1, "order_type": "MARKET", "sl_ticks": 0, "tp_ticks": 0})
    await asyncio.sleep(0.05)
    cur = container.metrics.current()
    assert cur["histograms"]["manual_order_ms"]["n"] == 1 and cur["counters"].get("manual_orders") == 1
