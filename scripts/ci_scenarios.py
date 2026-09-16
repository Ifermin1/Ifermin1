"""Escenarios de protección contra el NinjaTrader simulado, sin navegador (los usa CI).
Requiere fake_ninja.py --manual y el engine en modo ninja en :8000 con API_TOKEN=ci."""
import json
import sys
import time
import urllib.request

import zmq

API = "http://localhost:8000/api"
H = {"Authorization": "Bearer ci", "Content-Type": "application/json"}
ctx = zmq.Context()


def api(path: str, method: str = "GET", body: dict | None = None):
    req = urllib.request.Request(API + path, method=method, headers=H, data=json.dumps(body).encode() if body else None)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"null")


def ninja(cmd: str) -> str:
    s = ctx.socket(zmq.REQ); s.connect("tcp://127.0.0.1:5557"); s.send_string(cmd); r = s.recv_string(); s.close(); return r


def pos(acc: str) -> int:
    for a in api("/accounts"):
        if a["account_id"] == acc:
            return sum(p["quantity"] for p in a["open_positions"])
    return 0


failures = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global failures
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""), flush=True)
    failures += 0 if cond else 1


api("/accounts/Sim102/link", "PUT", {"master_account": "Sim101", "multiplier": 1})
api("/risk/limits", "PUT", {"account_id": "Sim102", "max_daily_loss": 500, "max_position_size": 3})
ninja("EMIT|BUY|2"); time.sleep(3.5)
check("entrada copiada 2/2", pos("Sim101") == 2 and pos("Sim102") == 2, f"({pos('Sim101')}/{pos('Sim102')})")
check("seq sin huecos", api("/health")["stats"]["seq_gaps"] == 0)
h = api("/health")["bridge"]
check("órdenes por canal con confirmación", h["order_channel"] == "req" and h["orders_confirmed"] >= 1, f"{h['order_channel']} {h['orders_confirmed']}")
ninja("EMIT|BUY|2"); time.sleep(3)
check("bloqueo por posición resultante", any(a["event_type"] == "BLOCKED" and "posición resultante" in a["message"] for a in api("/audit?limit=6")))
api("/accounts/Sim102/flatten", "POST", {"reason": "ci"}); time.sleep(6.5)
acc = next(a for a in api("/accounts") if a["account_id"] == "Sim102")
check("DESYNC detectado", acc["desync"] is True, acc["desync_detail"])
r = api("/accounts/Sim102/resync", "POST"); time.sleep(4)
acc = next(a for a in api("/accounts") if a["account_id"] == "Sim102")
check("resync iguala y limpia DESYNC", acc["desync"] is False and pos("Sim101") == pos("Sim102"), str(r["sent"]))
ninja("SET_PNL|Sim102|-520"); time.sleep(3.5)
risk = api("/risk")
check("límite diario pausa y cierra", risk["limits"][0]["trading_halted"] and risk["limits"][0]["halted_reason"] == "daily_loss" and pos("Sim102") == 0)
ninja("SET_PNL|Sim102|0"); time.sleep(3)
api("/risk/limits", "PUT", {"account_id": "Sim102", "max_daily_loss": 500, "max_daily_profit": 800, "max_position_size": 3, "trading_halted": False})
ninja("SET_PNL|Sim102|850"); time.sleep(3.5)
risk = api("/risk")
check("objetivo de ganancia pausa", risk["limits"][0]["trading_halted"] and risk["limits"][0]["halted_reason"] == "daily_profit")
ninja("SET_PNL|Sim102|0"); time.sleep(3)
api("/risk/limits", "PUT", {"account_id": "Sim102", "max_daily_loss": 500, "max_daily_profit": 800, "max_position_size": 3, "trading_halted": False})
# canal de eventos atascado: el addon publica pero no llega nada -> reconexión en segundos y WATCH de nuevo
api("/accounts/Sim102/resync", "POST"); time.sleep(4)          # volver a igualar tras los cierres por límite
ninja("MUTE|9"); time.sleep(9.5)
audit = api("/audit?limit=12")
check("canal atascado detectado y reconectado", any(a["event_type"] == "RESUBSCRIBE" for a in audit)
      and any(a["event_type"] == "ADDON_RECOVERY" for a in audit))
before = pos("Sim102")
ninja("EMIT|SELL|1"); time.sleep(3.5)
check("tras la reconexión se sigue copiando", pos("Sim102") == before - 1, f"({before} -> {pos('Sim102')})")
api("/risk/kill-switch", "POST", {"active": True, "reason": "ci", "flatten": True, "flatten_master": True}); time.sleep(3)
check("kill switch cierra todo", pos("Sim101") == 0 and pos("Sim102") == 0 and api("/risk")["kill_switch"])
api("/risk/kill-switch", "POST", {"active": False})
types = {a["event_type"] for a in api("/audit?limit=80")}
check("auditoría completa", {"REPLICATED", "FOLLOWER_FILL", "DESYNC", "SYNC_ORDER", "RESYNC", "DAILY_LOSS_LIMIT", "DAILY_PROFIT_TARGET", "FLATTEN", "FLATTENED", "RESUBSCRIBE", "ADDON_RECOVERY"} <= types, ",".join(sorted(types)))
sys.exit(1 if failures else 0)
