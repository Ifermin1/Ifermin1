"""Métricas: latencias y contadores por hora, guardados 30 días, y salida Prometheus.

Histogramas (ms): `copy_engine_ms` (del evento del maestro al envío), `copy_dispatch_ms` (envío → confirmación del addon),
`copy_fill_ms` (del fill del maestro al fill de la seguidora), `manual_order_ms`, `sync_ms`, `ping_ms`.
Contadores: fills, orders_out, blocked, rejected, errors, manual_orders, manual_rejected, disconnects, addon_restarts,
seq_gaps, degraded, no_ticks (la mayoría se cuentan desde la auditoría, así no hay que tocar cada servicio).
Cada hora se consolidan (n, media, mín, p50, p95, máx) en `metrics_hourly`; `GET /api/metrics` los expone en formato
Prometheus y `GET /api/metrics/summary` para la pestaña Salud.
"""
import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta

from loguru import logger

from tradepilot.core.events import TOPIC_AUDIT, EventBus

HISTOGRAMS = ("copy_engine_ms", "copy_dispatch_ms", "copy_fill_ms", "manual_order_ms", "sync_ms", "ping_ms")
AUDIT_COUNTERS = {"FOLLOWER_FILL": "fills", "FOLLOWER_REJECTED": "rejected", "BLOCKED": "blocked", "TRIMMED": "blocked", "ERROR": "errors", "ENTRY_MISSED": "rejected",
                  "ADDON_SILENT": "disconnects", "ADDON_DOWN": "disconnects", "ADDON_RESTART": "addon_restarts", "GAP": "seq_gaps", "MANUAL_ORDER": "manual_orders",
                  "MANUAL_REJECTED": "manual_rejected", "DEGRADED_ON": "degraded", "NO_TICKS": "no_ticks", "DESYNC": "desyncs", "NAKED_CLOSE": "errors", "RESUBSCRIBE": "disconnects"}
MAX_SAMPLES = 5000
KEEP_DAYS = 30


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def hour_key(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time()).strftime("%Y-%m-%dT%H:00")


class MetricsService:
    def __init__(self, store, bus: EventBus | None = None, bridge=None, accounts=None, risk=None, hub=None, watchdog=None) -> None:
        self.store, self.bridge, self.accounts, self.risk, self.hub, self.watchdog = store, bridge, accounts, risk, hub, watchdog
        self.hour = hour_key()
        self.samples: dict[str, list[float]] = defaultdict(list)
        self.counters: dict[str, int] = defaultdict(int)
        self.totals: dict[str, int] = defaultdict(int)       # desde el arranque
        self._task: asyncio.Task | None = None
        self.started_at = time.time()
        if bus is not None:
            bus.subscribe(TOPIC_AUDIT, self._on_audit)

    # ---- entrada ----
    def observe(self, name: str, value_ms: float | None) -> None:
        if value_ms is None:
            return
        self._roll_if_needed()
        s = self.samples[name]
        s.append(float(value_ms))
        if len(s) > MAX_SAMPLES:
            del s[: len(s) - MAX_SAMPLES]

    def inc(self, name: str, n: int = 1) -> None:
        self._roll_if_needed()
        self.counters[name] += n
        self.totals[name] += n

    async def _on_audit(self, event: dict) -> None:
        name = AUDIT_COUNTERS.get(str(event.get("event_type", "")))
        if name:
            self.inc(name)

    # ---- consolidación por hora ----
    def _agg(self, vals: list[float]) -> dict:
        s = sorted(vals)
        return {"n": len(s), "avg": round(sum(s) / len(s), 1) if s else 0.0, "min": s[0] if s else 0.0, "p50": round(_pct(s, 0.5), 1), "p95": round(_pct(s, 0.95), 1), "max": s[-1] if s else 0.0}

    def current(self) -> dict:
        """Lo que lleva la hora en curso."""
        return {"hour": self.hour, "histograms": {k: self._agg(v) for k, v in self.samples.items() if v}, "counters": dict(self.counters)}

    def _roll_if_needed(self) -> None:
        h = hour_key()
        if h != self.hour:
            self.flush(final=True)
            self.hour = h
            self.samples.clear()
            self.counters.clear()

    def flush(self, final: bool = False) -> None:
        """Guarda la hora en curso (se llama cada minuto y al cambiar de hora; sobrescribe la fila)."""
        if self.store is None:
            return
        rows = []
        for k, v in self.samples.items():
            if v:
                a = self._agg(v)
                rows.append((self.hour, k, a["n"], round(sum(v), 1), a["min"], a["max"], a["p50"], a["p95"]))
        for k, n in self.counters.items():
            rows.append((self.hour, k, n, float(n), 0.0, 0.0, 0.0, 0.0))
        if rows:
            try:
                self.store.metrics_upsert(rows)
            except Exception as exc:
                logger.warning(f"Métricas: no se pudieron guardar: {exc}")
        if final:
            try:
                self.store.metrics_prune((datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:00"))
            except Exception:
                pass

    async def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.flush()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                self.sample_gauges()
                self._roll_if_needed()
                self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Métricas: {exc}")

    def sample_gauges(self) -> None:
        h = self.bridge.health if self.bridge is not None else None
        if h is not None and h.ping_ms is not None:
            self.observe("ping_ms", h.ping_ms)

    # ---- salida ----
    def gauges(self) -> dict:
        h = self.bridge.health if self.bridge is not None else None
        rs = self.risk.state() if self.risk is not None else None
        accs = list(self.accounts.accounts.values()) if self.accounts is not None else []
        return {"connected": bool(h.connected) if h else False, "addon_version": h.addon_version if h else None, "ping_ms": h.ping_ms if h else None,
                "last_order_ms": h.last_order_ms if h else None, "kill_switch": bool(rs.kill_switch) if rs else False, "day_closed": bool(rs.day_closed) if rs else False,
                "accounts_enabled": sum(1 for a in accs if a.enabled), "positions": sum(1 for a in accs if a.enabled for p in a.open_positions if p.quantity),
                "day_total": self.risk.day_total() if self.risk is not None else 0.0, "hub_queued": self.hub.state()["queued"] if self.hub is not None and self.hub.enabled else 0,
                "degraded": bool(self.watchdog.degraded) if self.watchdog is not None else False, "no_ticks": bool(self.watchdog.no_ticks) if self.watchdog is not None else False,
                "loop_lag_ms": self.watchdog.loop_lag_ms if self.watchdog is not None else 0, "uptime_s": round(time.time() - self.started_at)}

    def summary(self, hours: int = 168) -> dict:
        since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:00")
        rows = self.store.metrics_range(since) if self.store is not None else []
        hourly: dict[str, dict] = defaultdict(dict)
        for r in rows:
            hourly[r["hour"]][r["metric"]] = {"n": r["n"], "sum": r["sum"], "min": r["min"], "max": r["max"], "p50": r["p50"], "p95": r["p95"]}
        cur = self.current()
        for k, a in cur["histograms"].items():
            hourly[cur["hour"]][k] = {"n": a["n"], "sum": round(a["avg"] * a["n"], 1), "min": a["min"], "max": a["max"], "p50": a["p50"], "p95": a["p95"]}
        for k, n in cur["counters"].items():
            hourly[cur["hour"]][k] = {"n": n, "sum": float(n), "min": 0, "max": 0, "p50": 0, "p95": 0}
        daily: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for h, metrics in hourly.items():
            day = h[:10]
            for k, a in metrics.items():
                if k in HISTOGRAMS:
                    daily[day][k + "_p95"] = max(daily[day][k + "_p95"], a["p95"])
                    daily[day][k + "_n"] += a["n"]
                else:
                    daily[day][k] += a["n"]
        return {"since": since, "gauges": self.gauges(), "current": cur, "totals": dict(self.totals),
                "hourly": [{"hour": h, **{k: v for k, v in m.items()}} for h, m in sorted(hourly.items())],
                "daily": [{"day": d, **dict(v)} for d, v in sorted(daily.items())]}

    def prometheus(self) -> str:
        g = self.gauges()
        lines = ["# HELP tradepilot_up 1 si el engine responde", "# TYPE tradepilot_up gauge", "tradepilot_up 1"]
        for k, v in g.items():
            if isinstance(v, bool):
                lines += [f"# TYPE tradepilot_{k} gauge", f"tradepilot_{k} {int(v)}"]
            elif isinstance(v, (int, float)) and v is not None:
                lines += [f"# TYPE tradepilot_{k} gauge", f"tradepilot_{k} {v}"]
        for k, n in sorted(self.totals.items()):
            lines += [f"# TYPE tradepilot_{k}_total counter", f"tradepilot_{k}_total {n}"]
        cur = self.current()["histograms"]
        for k, a in sorted(cur.items()):
            lines += [f"# TYPE tradepilot_{k} summary"]
            for q, key in (("0.5", "p50"), ("0.95", "p95")):
                lines.append(f'tradepilot_{k}{{quantile="{q}"}} {a[key]}')
            lines.append(f"tradepilot_{k}_count {a['n']}")
            lines.append(f"tradepilot_{k}_sum {round(a['avg'] * a['n'], 1)}")
        return "\n".join(lines) + "\n"
