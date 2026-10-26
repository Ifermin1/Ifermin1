"""Pruebas de caos contra el NinjaTrader simulado (las usa CI; también valen en local).

Arranca fake_ninja.py --manual y el engine en modo ninja (puerto 8000, token "ci"), y provoca lo que sale mal en la vida
real: fills duplicados, ráfagas, addon que se reinicia, NinjaTrader que deja de responder (modo degradado), órdenes
manuales repetidas, y el engine matado y arrancado de nuevo (idempotencia y reconciliación). Cada comprobación imprime
PASS/FAIL; sale con 1 si alguna falla.

    python scripts/ci_chaos.py            (desde la raíz del repo)
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import zmq

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000/api"
H = {"Authorization": "Bearer ci", "Content-Type": "application/json"}
DB = Path(os.environ.get("CHAOS_DB", "/tmp/chaos.db" if os.name != "nt" else str(ROOT / "engine" / "data" / "chaos.db")))
ctx = zmq.Context()
failures = 0
procs: list[subprocess.Popen] = []


def api(path: str, method: str = "GET", body: dict | None = None, timeout: float = 10):
    req = urllib.request.Request(API + path, method=method, headers=H, data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def ninja(cmd: str, timeout_ms: int = 5000) -> str:
    s = ctx.socket(zmq.REQ); s.setsockopt(zmq.RCVTIMEO, timeout_ms); s.setsockopt(zmq.LINGER, 0); s.connect("tcp://127.0.0.1:5557")
    try:
        s.send_string(cmd); return s.recv_string()
    finally:
        s.close()


def pos(acc: str) -> int:
    for a in api("/accounts"):
        if a["account_id"] == acc:
            return sum(p["quantity"] for p in a["open_positions"])
    return 0


def audit_types(limit: int = 60) -> list[str]:
    return [a["event_type"] for a in api(f"/audit?limit={limit}")]


def check(name: str, cond: bool, extra: str = "") -> None:
    global failures
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""), flush=True)
    failures += 0 if cond else 1


def wait_api(seconds: float = 25) -> bool:
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            api("/health", timeout=2); return True
        except Exception:
            time.sleep(0.5)
    return False


def start_engine() -> subprocess.Popen:
    env = {**os.environ, "ENGINE_MODE": "ninja", "API_TOKEN": "ci", "API_PORT": "8000", "DB_PATH": str(DB), "JOURNAL_DIR": "", "WEB_DIST": "/none",
           "DESYNC_GRACE_SECONDS": "3", "WATCHDOG_DEGRADED_AFTER_S": "5", "WATCHDOG_REMIND_S": "5", "BACKUP_INTERVAL_H": "9999", "MOCK_DEMO_HISTORY": "false"}
    p = subprocess.Popen([sys.executable, "-m", "tradepilot.main"], cwd=str(ROOT / "engine"), env=env, stdout=open(ROOT / "engine-chaos.log", "ab"), stderr=subprocess.STDOUT)
    procs.append(p)
    assert wait_api(), "el engine no arrancó"
    time.sleep(3)   # primera sincronización
    return p


def kill(p: subprocess.Popen) -> None:
    if os.name == "nt":
        p.kill()
    else:
        p.send_signal(signal.SIGKILL)
    p.wait(timeout=10)
    time.sleep(1)


def main() -> int:
    if DB.exists():
        DB.unlink()
    fake = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "fake_ninja.py"), "--manual"], stdout=open(ROOT / "fake-chaos.log", "ab"), stderr=subprocess.STDOUT)
    procs.append(fake)
    time.sleep(1.5)
    eng = start_engine()
    api("/accounts/Sim102/link", "PUT", {"master_account": "Sim101", "multiplier": 1})
    api("/risk/limits", "PUT", {"account_id": "Sim102", "max_daily_loss": 5000, "max_position_size": 10})
    time.sleep(1)

    # 1) fill duplicado: el mismo EXECUTION dos veces -> se copia una sola vez
    ninja("DUP|BUY|1"); time.sleep(3)
    check("fill duplicado se copia una sola vez", pos("Sim101") == 1 and pos("Sim102") == 1, f"({pos('Sim101')}/{pos('Sim102')})")
    check("duplicado contado", api("/health")["stats"]["duplicates"] >= 1)

    # 2) ráfaga: cinco fills seguidos sin pausa -> cinco copias, sin huecos de secuencia
    ninja("BURST|BUY|1|5"); time.sleep(4)
    check("ráfaga de 5 fills copiada entera", pos("Sim101") == 6 and pos("Sim102") == 6, f"({pos('Sim101')}/{pos('Sim102')})")
    check("sin huecos de secuencia", api("/health")["stats"]["seq_gaps"] == 0)

    # 3) órdenes manuales con el mismo id (en la maestra: se replica a la seguidora): la segunda es duplicada, no se envía
    o = {"client_id": "chaos-1", "account": "Sim101", "symbol": "NQ 12-26", "action": "SELL", "quantity": 1, "order_type": "MARKET", "sl_ticks": 0, "tp_ticks": 0}
    r1 = api("/trade/order", "POST", o); time.sleep(3); r2 = api("/trade/order", "POST", o); time.sleep(1.5)
    check("orden manual repetida no duplica", r2.get("duplicate") is True and r1["order_id"] == r2["order_id"] and pos("Sim101") == 5 and pos("Sim102") == 5, f"({pos('Sim101')}/{pos('Sim102')})")

    # 4) el addon se reinicia a mitad: boot nuevo, seq a 0 -> ADDON_RESTART y la copia sigue
    ninja("REBOOT"); time.sleep(8)
    check("reinicio del addon detectado", "ADDON_RESTART" in audit_types(40) or "RESUBSCRIBE" in audit_types(40), str(audit_types(12)))
    ninja("EMIT|SELL|1"); time.sleep(3.5)
    check("tras el reinicio del addon se sigue copiando", pos("Sim101") == 4 and pos("Sim102") == 4, f"({pos('Sim101')}/{pos('Sim102')})")

    # 5) NinjaTrader deja de responder 25 s: sin heartbeat -> ADDON_SILENT -> modo degradado; al volver, DEGRADED_OFF y reconciliación
    ninja("SLEEP|25")
    t0 = time.time(); degraded = False
    while time.time() - t0 < 40:
        h = api("/health")["watchdog"]
        if h and h["degraded"]:
            degraded = True; break
        time.sleep(1)
    check("modo degradado al dejar de responder NinjaTrader", degraded)
    t0 = time.time(); back = False
    while time.time() - t0 < 45:
        h = api("/health")["watchdog"]
        if h and not h["degraded"] and "DEGRADED_OFF" in audit_types(30):
            back = True; break
        time.sleep(1)
    check("salida del modo degradado y reconciliación al volver", back and "RECONCILE" in audit_types(40), str(audit_types(10)))
    ninja("EMIT|BUY|1"); time.sleep(3.5)
    check("tras el cuelgue se sigue copiando", pos("Sim101") == 5 and pos("Sim102") == 5, f"({pos('Sim101')}/{pos('Sim102')})")

    # 6) engine matado y arrancado: idempotencia persistida, reconciliación de arranque, posiciones intactas
    kill(eng)
    eng = start_engine()
    r3 = api("/trade/order", "POST", o)
    check("tras reiniciar el engine la orden repetida sigue siendo duplicada", r3.get("duplicate") is True and r3["order_id"] == r1["order_id"])
    rep = api("/reconcile")
    check("reconciliación de arranque hecha", rep["reason"] == "arranque" and rep["ran_at"] is not None, json.dumps(rep["counts"]))
    check("posiciones intactas tras el reinicio", pos("Sim101") == 5 and pos("Sim102") == 5, f"({pos('Sim101')}/{pos('Sim102')})")
    ninja("EMIT|SELL|1"); time.sleep(3.5)
    check("la copia sigue tras reiniciar el engine", pos("Sim101") == 4 and pos("Sim102") == 4, f"({pos('Sim101')}/{pos('Sim102')})")
    # 7) salud y métricas con datos
    m = api("/metrics/summary?hours=24")
    check("métricas con latencias y fills", m["totals"].get("fills", 0) >= 1 and "copy_fill_ms" in m["current"]["histograms"])
    d = api("/doctor")
    check("doctor responde", d["counts"]["ok"] >= 3)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
    print(f"\n{'OK' if code == 0 else 'FALLOS'}: {failures} comprobaciones fallidas")
    sys.exit(code)
