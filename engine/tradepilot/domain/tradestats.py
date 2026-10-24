"""Estadísticas de operaciones en el engine (mismas definiciones que web/src/lib/stats.ts) para la revisión de la sesión,
el semáforo y el chat con los datos. Funciones puras sobre filas de la tabla `trades`."""
from collections import defaultdict
from datetime import datetime


def net_of(t: dict) -> float:
    return round(float(t.get("pnl") or 0) - float(t.get("commissions") or 0), 2)


def seconds_of(t: dict) -> float | None:
    try:
        if not t.get("opened_at"):
            return None
        return max(0.0, (datetime.fromisoformat(t["closed_at"]) - datetime.fromisoformat(t["opened_at"])).total_seconds())
    except (TypeError, ValueError):
        return None


def trade_stats(trades: list[dict]) -> dict:
    vals = [net_of(t) for t in trades]
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gp, gl = sum(wins), -sum(losses)
    ordered = sorted(trades, key=lambda t: t.get("closed_at") or "")
    max_w = max_l = cur = 0
    for v in (net_of(t) for t in ordered):
        if v > 0:
            cur = cur + 1 if cur > 0 else 1
            max_w = max(max_w, cur)
        elif v < 0:
            cur = cur - 1 if cur < 0 else -1
            max_l = max(max_l, -cur)
    durs_w = [s for s in (seconds_of(t) for t in trades if net_of(t) > 0) if s is not None]
    durs_l = [s for s in (seconds_of(t) for t in trades if net_of(t) < 0) if s is not None]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    return {"n": len(trades), "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / len(trades), 4) if trades else None,
            "net": round(sum(vals), 2), "gross_profit": round(gp, 2), "gross_loss": round(gl, 2), "pf": round(gp / gl, 2) if gl > 0 else None,
            "expectancy": avg(vals), "avg_win": avg(wins), "avg_loss": avg(losses), "largest_win": max(wins) if wins else 0, "largest_loss": min(losses) if losses else 0,
            "max_win_streak": max_w, "max_loss_streak": max_l, "current_streak": cur, "avg_dur_win_s": avg(durs_w), "avg_dur_loss_s": avg(durs_l),
            "contracts": sum(int(t.get("quantity") or 0) for t in trades)}


def bucket(trades: list[dict], key) -> list[dict]:
    g: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        g[str(key(t))].append(t)
    out = []
    for k, ts in g.items():
        vals = [net_of(t) for t in ts]
        out.append({"key": k, "n": len(ts), "net": round(sum(vals), 2), "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 3), "avg": round(sum(vals) / len(vals), 2)})
    return out


def by_hour(trades: list[dict]) -> list[dict]:
    def h(t):
        try:
            return datetime.fromisoformat(t.get("opened_at") or t["closed_at"]).strftime("%H")
        except (TypeError, ValueError):
            return "?"
    return sorted(bucket(trades, h), key=lambda b: b["key"])


WD = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def by_weekday(trades: list[dict]) -> list[dict]:
    def w(t):
        try:
            return WD[datetime.fromisoformat(t["day"]).weekday()]
        except (TypeError, ValueError):
            return "?"
    order = {k: i for i, k in enumerate(WD)}
    return sorted(bucket(trades, w), key=lambda b: order.get(b["key"], 9))


def by_symbol(trades: list[dict]) -> list[dict]:
    return sorted(bucket(trades, lambda t: str(t.get("symbol") or "").split(" ")[0].upper()), key=lambda b: -b["n"])


def by_account(trades: list[dict]) -> list[dict]:
    return sorted(bucket(trades, lambda t: t.get("account_id")), key=lambda b: -b["net"])


def by_side(trades: list[dict]) -> list[dict]:
    return bucket(trades, lambda t: "largos" if t.get("side") == "long" else "cortos")


def worst(trades: list[dict], k: int = 3) -> list[dict]:
    return sorted(trades, key=net_of)[:k]


def best(trades: list[dict], k: int = 3) -> list[dict]:
    return sorted(trades, key=net_of, reverse=True)[:k]


def brief(t: dict) -> str:
    d = seconds_of(t)
    return (f"{(t.get('closed_at') or '')[11:16]} {t.get('account_id')} {t.get('side')} {t.get('quantity')} {str(t.get('symbol') or '').split(' ')[0]} "
            f"{net_of(t):+.0f} $" + (f" ({int(d)} s)" if d is not None else ""))
