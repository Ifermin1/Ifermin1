"""TradePilot X Hub (fase 1): un servidor pequeño que reúne varios engines.

Cada engine con HUB_URL/HUB_TOKEN manda sus operaciones cerradas, una foto de su estado y los avisos importantes a
`POST /api/ingest`. El hub guarda todo en SQLite y expone `GET /api/summary` (nodos, cuentas, P&L del día, operaciones de
la semana), `GET /api/trades`, `GET /api/events` y una página mínima en `/`. Arranque: ver hub/README.md.
"""
import json
import os
import secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

_bearer = HTTPBearer(auto_error=False)


class HubStore:
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, mode TEXT, version TEXT, first_seen TEXT, last_seen TEXT, snapshot TEXT, snapshot_at TEXT);
                CREATE TABLE IF NOT EXISTS trades (node_id TEXT, trade_id INTEGER, account_id TEXT, day TEXT, symbol TEXT, side TEXT, quantity INTEGER,
                    entry_price REAL, exit_price REAL, pnl REAL, commissions REAL, opened_at TEXT, closed_at TEXT, received_at TEXT, PRIMARY KEY (node_id, trade_id));
                CREATE INDEX IF NOT EXISTS idx_hub_trades_day ON trades(day);
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT, ref TEXT, at TEXT, event_type TEXT, account TEXT, message TEXT, UNIQUE (node_id, ref));
            """)

    def ingest(self, node: dict, items: list[dict]) -> dict:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        nid = str(node.get("id") or "")[:80]
        counts = {"trade": 0, "snapshot": 0, "event": 0, "ignored": 0}
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO nodes (id, mode, version, first_seen, last_seen) VALUES (?, ?, ?, ?, ?) "
                               "ON CONFLICT(id) DO UPDATE SET mode = excluded.mode, version = excluded.version, last_seen = excluded.last_seen",
                               (nid, str(node.get("mode") or ""), str(node.get("version") or ""), now, now))
            for it in items:
                kind, data = it.get("kind"), it.get("data") or {}
                if kind == "trade" and data.get("id") is not None:
                    self._conn.execute("INSERT OR IGNORE INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                       (nid, int(data["id"]), data.get("account_id"), data.get("day"), data.get("symbol"), data.get("side"), data.get("quantity"),
                                        data.get("entry_price"), data.get("exit_price"), data.get("pnl"), data.get("commissions"), data.get("opened_at"), data.get("closed_at"), now))
                    counts["trade"] += 1
                elif kind == "snapshot":
                    self._conn.execute("UPDATE nodes SET snapshot = ?, snapshot_at = ? WHERE id = ?", (json.dumps(data, ensure_ascii=False), it.get("at") or now, nid))
                    counts["snapshot"] += 1
                elif kind == "event":
                    ref = str(it.get("ref")) if it.get("ref") is not None else f"{it.get('at')}|{data.get('event_type')}|{str(data.get('message'))[:40]}"
                    self._conn.execute("INSERT OR IGNORE INTO events (node_id, ref, at, event_type, account, message) VALUES (?, ?, ?, ?, ?, ?)",
                                       (nid, ref, data.get("timestamp") or it.get("at") or now, data.get("event_type"), data.get("account"), str(data.get("message") or "")[:400]))
                    counts["event"] += 1
                else:
                    counts["ignored"] += 1
        return counts

    def nodes(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM nodes ORDER BY id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["snapshot"] = json.loads(d["snapshot"]) if d.get("snapshot") else None
            out.append(d)
        return out

    def trades(self, day_from: str, day_to: str, node: str | None = None, limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM trades WHERE day >= ? AND day <= ?", [day_from, day_to]
        if node:
            sql += " AND node_id = ?"
            args.append(node)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY closed_at DESC LIMIT ?", args + [int(limit)]).fetchall()
        return [dict(r) for r in rows]

    def events(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]


class Item(BaseModel):
    kind: str
    ref: int | str | None = None
    at: str | None = None
    data: dict = Field(default_factory=dict)


class Ingest(BaseModel):
    node: dict
    items: list[Item] = Field(default_factory=list, max_length=1000)


def _stats(trades: list[dict]) -> dict:
    net = [float(t["pnl"] or 0) - float(t["commissions"] or 0) for t in trades]
    wins = sum(1 for v in net if v > 0)
    return {"n": len(trades), "net": round(sum(net), 2), "wins": wins, "win_rate": round(wins / len(trades), 3) if trades else None}


def create_app(db_path: str | None = None, token: str | None = None) -> FastAPI:
    token = (token if token is not None else os.environ.get("HUB_TOKEN", "")).strip()
    db = db_path or os.environ.get("HUB_DB", str(Path(__file__).parent / "hub.db"))
    store = HubStore(db)
    app = FastAPI(title="TradePilot X Hub", version="0.1")
    app.state.store = store

    def auth(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
        if not token:
            raise HTTPException(503, "HUB_TOKEN no configurado en el hub")
        got = creds.credentials if creds else request.query_params.get("token")
        if not got or not secrets.compare_digest(got, token):
            raise HTTPException(401, "token no válido")

    @app.post("/api/ingest", dependencies=[Depends(auth)])
    def ingest(body: Ingest):
        if not str(body.node.get("id") or "").strip():
            raise HTTPException(400, "node.id obligatorio")
        counts = store.ingest(body.node, [i.model_dump() for i in body.items])
        return {"ok": True, "accepted": sum(v for k, v in counts.items() if k != "ignored"), "counts": counts}

    @app.get("/api/summary", dependencies=[Depends(auth)])
    def summary():
        today = date.today()
        monday = (today - timedelta(days=today.weekday())).isoformat()
        week = store.trades(monday, today.isoformat(), limit=100000)
        day = [t for t in week if t["day"] == today.isoformat()]
        nodes = []
        for n in store.nodes():
            s = n.get("snapshot") or {}
            try:
                stale = (datetime.now().astimezone() - datetime.fromisoformat(n["last_seen"])).total_seconds() > 180 if n.get("last_seen") else True
            except ValueError:
                stale = True
            nodes.append({"id": n["id"], "mode": n["mode"], "version": n["version"], "last_seen": n["last_seen"], "stale": stale,
                          "day_total": s.get("day_total"), "kill_switch": s.get("kill_switch"), "day_closed": s.get("day_closed"), "connected": s.get("connected"),
                          "accounts": [{"account_id": a["account_id"], "alias": a.get("alias"), "net_pnl": a.get("net_pnl"), "balance": a.get("balance"),
                                        "positions": len(a.get("positions") or []), "enabled": a.get("enabled"), "drawdown": a.get("drawdown"), "drawdown_limit": a.get("drawdown_limit")}
                                       for a in s.get("accounts") or []],
                          "today": _stats([t for t in day if t["node_id"] == n["id"]]), "week": _stats([t for t in week if t["node_id"] == n["id"]])})
        return {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), "nodes": nodes, "today": _stats(day), "week": _stats(week),
                "day_total": round(sum(float(n["day_total"] or 0) for n in nodes), 2), "events": store.events(20)}

    @app.get("/api/trades", dependencies=[Depends(auth)])
    def trades(days: int = 7, node: str | None = None):
        today = date.today()
        return store.trades((today - timedelta(days=max(0, days - 1))).isoformat(), today.isoformat(), node)

    @app.get("/api/events", dependencies=[Depends(auth)])
    def events(limit: int = 50):
        return store.events(min(max(1, limit), 500))

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    return app


PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>TradePilot X Hub</title>
<style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0b1220;color:#e5e7eb;margin:0;padding:18px;max-width:1100px;margin:auto}
h1{font-size:20px;margin:0 0 10px} .muted{color:#94a3b8;font-size:12px} .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.kpi{background:#111a2e;border:1px solid #1f2a44;border-radius:10px;padding:10px} .kpi small{display:block;color:#94a3b8;font-size:11px;text-transform:uppercase} .kpi b{font-size:20px}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px} th,td{padding:6px 8px;border-bottom:1px solid #1f2a44;text-align:right;white-space:nowrap} th:first-child,td:first-child{text-align:left}
.pos{color:#34d399} .neg{color:#f87171} .bad{color:#f87171;font-weight:700} .node{background:#111a2e;border:1px solid #1f2a44;border-radius:12px;padding:12px;margin:10px 0}
input{background:#0f172a;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:8px} button{background:#2563eb;color:#fff;border:0;border-radius:8px;padding:8px 14px}</style></head>
<body><h1>TradePilot X · Hub</h1><div id="login"><p class="muted">Token del hub (HUB_TOKEN):</p><input id="tok" type="password" placeholder="token"> <button onclick="save()">Entrar</button></div>
<div id="app" style="display:none"></div>
<script>
const K='hub.token'; const usd=v=>(v==null?'—':(v>0?'+':'')+Number(v).toLocaleString('es-ES',{maximumFractionDigits:0})+' $'); const cls=v=>v>0?'pos':v<0?'neg':'';
function save(){localStorage.setItem(K,document.getElementById('tok').value.trim());load();}
async function load(){const t=localStorage.getItem(K);if(!t)return;const r=await fetch('/api/summary',{headers:{Authorization:'Bearer '+t}});if(!r.ok){localStorage.removeItem(K);document.getElementById('login').style.display='';return;}
const d=await r.json();document.getElementById('login').style.display='none';const app=document.getElementById('app');app.style.display='';
app.innerHTML=`<div class="kpis"><div class="kpi"><small>P&L hoy (todos)</small><b class="${cls(d.day_total)}">${usd(d.day_total)}</b></div><div class="kpi"><small>Operaciones hoy</small><b>${d.today.n}</b> <span class="muted">${usd(d.today.net)}</span></div><div class="kpi"><small>Semana</small><b>${d.week.n}</b> <span class="muted">${usd(d.week.net)} · acierto ${d.week.win_rate==null?'—':Math.round(d.week.win_rate*100)+' %'}</span></div><div class="kpi"><small>Engines</small><b>${d.nodes.length}</b> <span class="muted">${d.nodes.filter(n=>n.stale).length} sin señal</span></div></div>`
+d.nodes.map(n=>`<div class="node"><b>${n.id}</b> <span class="muted">${n.mode||''} ${n.version||''} · visto ${n.last_seen?n.last_seen.slice(11,16):'—'}</span> ${n.stale?'<span class="bad">SIN SEÑAL</span>':''} ${n.kill_switch?'<span class="bad">KILL SWITCH</span>':''} ${n.connected===false?'<span class="bad">sin NinjaTrader</span>':''}
<span style="float:right" class="${cls(n.day_total)}"><b>${usd(n.day_total)}</b> hoy</span>
<table><tr><th>Cuenta</th><th>P&L hoy</th><th>Saldo</th><th>Pos.</th><th>Drawdown</th></tr>${n.accounts.filter(a=>a.enabled!==false).map(a=>`<tr><td>${a.alias||a.account_id}</td><td class="${cls(a.net_pnl)}">${usd(a.net_pnl)}</td><td>${a.balance==null?'—':Number(a.balance).toLocaleString('es-ES',{maximumFractionDigits:0})+' $'}</td><td>${a.positions||''}</td><td>${a.drawdown_limit?Math.round(a.drawdown/a.drawdown_limit*100)+' %':'—'}</td></tr>`).join('')}</table>
<div class="muted" style="margin-top:6px">hoy ${n.today.n} op. ${usd(n.today.net)} · semana ${n.week.n} op. ${usd(n.week.net)}</div></div>`).join('')
+`<h3>Últimos avisos</h3><table>${d.events.map(e=>`<tr><td>${(e.at||'').slice(5,16).replace('T',' ')}</td><td>${e.node_id}</td><td>${e.event_type}</td><td style="text-align:left;white-space:normal">${e.message}</td></tr>`).join('')||'<tr><td>—</td></tr>'}</table><p class="muted">actualizado ${d.generated_at.slice(11,19)} · se refresca cada 30 s</p>`;}
load(); setInterval(load,30000);
</script></body></html>"""

app = create_app()
