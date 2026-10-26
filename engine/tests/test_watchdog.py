"""Robustez 3: vigilante (suspensión, modo degradado, sin datos de mercado)."""
from datetime import datetime, timezone

from datetime import timedelta, timezone

from tradepilot.services.watchdog_service import _us_dst, cme_open, to_ny


def test_ny_by_rule_matches_zoneinfo_when_available():
    import tradepilot.services.watchdog_service as w
    for d in (datetime(2026, 1, 15, 12, tzinfo=timezone.utc), datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
              datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc), datetime(2026, 3, 8, 7, 1, tzinfo=timezone.utc),
              datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc), datetime(2026, 11, 1, 6, 1, tzinfo=timezone.utc)):
        by_rule = d.astimezone(timezone(timedelta(hours=-4 if _us_dst(d) else -5)))
        if w.NY is not None:
            assert by_rule.hour == d.astimezone(w.NY).hour and by_rule.day == d.astimezone(w.NY).day, d
        assert to_ny(d).hour == by_rule.hour


def test_cme_hours():
    assert cme_open(datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)) is True        # martes 10:30 NY
    assert cme_open(datetime(2026, 10, 27, 21, 30, tzinfo=timezone.utc)) is False       # martes 17:30 NY: pausa diaria
    assert cme_open(datetime(2026, 10, 27, 22, 30, tzinfo=timezone.utc)) is True        # martes 18:30 NY
    assert cme_open(datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)) is False        # sábado 11:00 NY
    assert cme_open(datetime(2026, 10, 30, 21, 30, tzinfo=timezone.utc)) is False       # viernes 17:30 NY: cerrado
    assert cme_open(datetime(2026, 11, 1, 22, 30, tzinfo=timezone.utc)) is False        # domingo 17:30 NY: aún no
    assert cme_open(datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc)) is True         # domingo 18:30 NY


def _events(container, kind):
    return [a for a in container.audit.recent(50) if a.event_type == kind]


async def test_degraded_mode_and_reminders(container):
    await container.accounts.sync_once()
    w = container.watchdog
    w.mode = "ninja"                      # fingimos ninja: el puente conectado y el addon mudo
    container.bridge.health.connected = True
    w.degraded_after_s, w.remind_s = 30, 600
    clock = [1_000_000.0]
    w.now = lambda: clock[0]
    w._last_wall = clock[0]
    container.risk.addon_silent = True
    await w.tick()
    assert not w.degraded                 # aún no han pasado 30 s
    clock[0] += 31
    await w.tick()
    assert w.degraded and _events(container, "DEGRADED_ON")
    clock[0] += 300
    await w.tick()
    assert not _events(container, "ADDON_STILL_DOWN")
    clock[0] += 301
    await w.tick()
    assert len(_events(container, "ADDON_STILL_DOWN")) == 1
    assert w.state()["degraded"] is True and w.state()["degraded_since"]
    container.risk.addon_silent = False
    await w.tick()
    assert not w.degraded and _events(container, "DEGRADED_OFF")


async def test_resume_after_suspension_resyncs_and_reconciles(container):
    await container.accounts.sync_once()
    w = container.watchdog
    clock = [2_000_000.0]
    mono = [100.0]
    w.now, w.mono = (lambda: clock[0]), (lambda: mono[0])
    w._last_wall, w._last_mono = clock[0], mono[0]
    clock[0] += 10; mono[0] += 10
    await w.tick()
    assert not _events(container, "ENGINE_RESUMED")
    clock[0] += 900; mono[0] += 10         # el reloj avanzó 15 min más que el proceso: el PC durmió
    await w.tick()
    ev = _events(container, "ENGINE_RESUMED")
    assert ev and "suspendido" in ev[0].message and w.last_resume["kind"] == "suspensión"
    assert container.reconcile.last and container.reconcile.last["reason"] == "reanudación"


async def test_no_ticks_only_when_market_open_and_connected(container):
    await container.accounts.sync_once()
    w = container.watchdog
    w.mode = "ninja"
    container.bridge.health.connected = True
    clock = [3_000_000.0]
    w.now = lambda: clock[0]
    w._last_wall = clock[0]
    w.started_at = clock[0]
    w.no_ticks_after_s, w.remind_s = 180, 600
    container.market.last_at = {"NQ": clock[0] - 10}
    w.market_open = lambda: False
    clock[0] += 400
    await w.tick()
    assert not w.no_ticks                 # mercado cerrado: sin aviso
    w.market_open = lambda: True
    await w.tick()
    assert w.no_ticks and _events(container, "NO_TICKS")
    container.market.last_at = {"NQ": clock[0]}
    await w.tick()
    assert not w.no_ticks and _events(container, "TICKS_BACK")
