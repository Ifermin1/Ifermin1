"""NinjaTrader falso para probar ENGINE_MODE=ninja sin NinjaTrader.

Imita el addon TradePilotXBridge:
  PUB 5555  eventos del maestro (EXECUTION / ORDER_*), HEARTBEAT, PRICE, POSITION
            y los ACK de los followers (ORDER_STATUS + EXECUTION con master_order_id)
  SUB 5556  recibe las órdenes que el engine manda al ejecutor
  REP 5557  responde a "GET_ACCOUNTS" con "Sim101|50000.00;Sim102|25000.00"

Uso:  python scripts/fake_ninja.py            (una operación del maestro cada 5 s)
      python scripts/fake_ninja.py --once     (una y termina)
      python scripts/fake_ninja.py --reject   (los followers rechazan las órdenes)
      python scripts/fake_ninja.py --old-addon (imita un addon sin GET_ACCOUNTS_ALL)
      python scripts/fake_ninja.py --manual   (no opera solo; petición "EMIT|BUY|1" por 5557 dispara una operación)
      python scripts/fake_ninja.py --no-fill-limits (las entradas límite quedan trabajando: prueba el fallback del addon)
  Ganchos por 5557: EMIT|BUY|2, PENDING|SELL|2|STOPMARKET|19950 (stop/TP del maestro), SET_PNL|cuenta|valor,
                    MUTE|segundos (simula un canal de eventos atascado)
"""
import json
import random
import sys
import time
import uuid
from datetime import datetime

import zmq

ACCOUNTS = {"Sim101": 50000.0, "Sim102": 25000.0}
# cuentas que NinjaTrader conoce pero no están conectadas ahora (solo salen con GET_ACCOUNTS_ALL)
OFFLINE = {"APEX-112924-1": 0.0, "MFFUEVREOD": 50162.72, "Backtest": 0.0, "Playback101": 0.0}
# NinjaTrader recuerda todas las cuentas históricas: simulamos un usuario con muchas evaluaciones
OFFLINE.update({f"APEX-112924-{i}": 0.0 for i in range(86, 250)})
OFFLINE.update({f"NarvaezIbrahimUPTN{i}": 0.0 for i in range(91021, 91060)})
CONNECTION = "MFF"
MASTER = "Sim101"
SYMBOL = "NQ 12-26"
POSITIONS: dict[tuple, int] = {}   # (cuenta, símbolo) -> qty con signo; se actualiza con los fills
PNL: dict[str, float] = {}         # P&L del día por cuenta (gancho de pruebas SET_PNL|cuenta|valor)
SEQ = 0
BOOT = uuid.uuid4().hex[:8]        # cambia en cada arranque, como en el addon v1.8
MUTE_UNTIL = 0.0                   # gancho de pruebas MUTE|N: durante N s publica "al vacío" (canal de eventos atascado)
ORDERS: dict[str, dict] = {}       # copias pendientes (stop / TP) vivas: fid -> orden (GET_ORDERS, v2.2)


def now() -> str:
    iso = datetime.now().astimezone().isoformat(timespec="microseconds")  # 2026-09-15T17:58:49.981841+00:00
    return iso[:26] + "0" + iso[26:]  # 7 decimales antes del offset, como .NET ToString("o")


ctx = zmq.Context()
pub = ctx.socket(zmq.PUB); pub.bind("tcp://*:5555")
sub = ctx.socket(zmq.SUB); sub.bind("tcp://*:5556"); sub.setsockopt_string(zmq.SUBSCRIBE, "")
rep = ctx.socket(zmq.REP); rep.bind("tcp://*:5557")
poller = zmq.Poller(); poller.register(rep, zmq.POLLIN); poller.register(sub, zmq.POLLIN)
print("fake NinjaTrader escuchando en 5555/5556/5557 (Ctrl+C para salir)")

once, reject, manual = "--once" in sys.argv, "--reject" in sys.argv, "--manual" in sys.argv
no_fill_limits = "--no-fill-limits" in sys.argv
price = 20000.0
next_emit, next_hb, next_price, emitted = time.time() + 2, time.time() + 5, time.time() + 0.25, 0


def send(obj: dict) -> None:
    global SEQ
    SEQ += 1
    if time.time() < MUTE_UNTIL:
        return                     # el addon "publica" (seq avanza) pero el engine no recibe nada
    pub.send_string(json.dumps({"seq": SEQ, **obj}))


def apply_fill(account: str, action: str, symbol: str, qty: int) -> None:
    sign = 1 if action.upper().startswith("BUY") else -1
    POSITIONS[(account, symbol)] = POSITIONS.get((account, symbol), 0) + sign * qty


def handle_order(raw: str, via: str) -> str:
    """Orden del engine: por 5556 (sin confirmación) o por 5557 como ORDER|json (v1.9, con confirmación)."""
    o = json.loads(raw)
    print(f"ORDEN RECIBIDA DEL ENGINE ({via}):", raw)
    fid = "F" + uuid.uuid4().hex[:6]
    base = {"account": o["account"], "action": o["action"], "symbol": o["symbol"], "quantity": o["quantity"],
            "order_type": o["order_type"], "order_id": fid, "master_order_id": o["master_order_id"], "timestamp": now()}
    is_limit_entry = o.get("entry_mode") == "limit"
    if is_limit_entry:
        print(f"  entrada límite: tolerancia {o.get('tolerance_ticks')} ticks, {o.get('entry_timeout_s')} s, luego {o.get('entry_fallback')}")
    key = (o["account"], o["master_order_id"])
    live = next((f for f, w in ORDERS.items() if (w["account"], w["master_order_id"]) == key), None)
    if o["msg_type"] == "ORDER_MODIFIED":
        if live:
            ORDERS[live].update(quantity=o["quantity"], limit_price=o.get("limit_price", 0), stop_price=o.get("stop_price", 0))
            send({**ORDERS[live], "msg_type": "ORDER_STATUS", "filled": 0, "price": 0, "state": "Working", "error": "", "native_error": "", "timestamp": now()})
        return "OK|ORDER_MODIFIED" if live else "IGNORED|sin orden trabajando"
    if o["msg_type"] == "ORDER_CANCELLED":
        if live:
            w = ORDERS.pop(live)
            send({**w, "msg_type": "ORDER_STATUS", "filled": 0, "price": 0, "state": "Cancelled", "error": "", "native_error": "", "timestamp": now()})
        return "OK|ORDER_CANCELLED" if live else "IGNORED|sin orden viva"
    if o["msg_type"] == "ORDER_PENDING" and not reject:
        # stop / TP copiado: queda vivo (no se ejecuta solo) y sale en GET_ORDERS
        ORDERS[fid] = {**base, "limit_price": o.get("limit_price", 0), "stop_price": o.get("stop_price", 0)}
        send({**ORDERS[fid], "msg_type": "ORDER_STATUS", "filled": 0, "price": 0, "state": "Working", "error": "", "native_error": ""})
        return "OK|ORDER_PENDING"
    if o["msg_type"] == "EXECUTION" and live:
        # el master ejecutó esa orden: la copia viva se ejecuta también (cancelar + mercado, como el addon v2.0)
        w = ORDERS.pop(live)
        send({**w, "msg_type": "ORDER_STATUS", "filled": w["quantity"], "price": price, "state": "Filled", "error": "", "native_error": "", "timestamp": now()})
        send({**w, "msg_type": "EXECUTION", "price": price, "state": "Filled", "execution_id": "E" + live, "timestamp": now()})
        apply_fill(w["account"], w["action"], w["symbol"], w["quantity"])
        return "OK|EXECUTION_RECONCILE"
    if reject:
        send({**base, "msg_type": "ORDER_STATUS", "filled": 0, "price": 0, "limit_price": 0, "stop_price": 0,
              "state": "Rejected", "error": "OrderRejected", "native_error": "Insufficient margin"})
    elif is_limit_entry and no_fill_limits:
        send({**base, "msg_type": "ORDER_STATUS", "filled": 0, "price": 0, "limit_price": o.get("price", 0), "stop_price": 0,
              "state": "Working", "error": "", "native_error": ""})
    else:
        send({**base, "msg_type": "ORDER_STATUS", "filled": o["quantity"], "price": price, "limit_price": 0,
              "stop_price": 0, "state": "Filled", "error": "", "native_error": ""})
        send({**base, "msg_type": "EXECUTION", "price": price, "state": "Filled", "execution_id": "E" + fid})
        apply_fill(o["account"], o["action"], o["symbol"], o["quantity"])
    return "OK|" + o["msg_type"]


while True:
    for sock, _ in poller.poll(100):
        if sock is rep:
            msg = rep.recv_string()
            if msg == "GET_ACCOUNTS":
                rep.send_string(";".join(f"{a}|{b:.2f}" for a, b in ACCOUNTS.items()))
            elif msg == "GET_MASTER":
                rep.send_string(MASTER)
            elif msg == "GET_ORDERS":
                rep.send_string(";".join(f"{w['account']}|{f}|{w['master_order_id']}|{w['action']}|{w['symbol']}|{w['quantity']}|0|{w['order_type']}|{w['limit_price']}|{w['stop_price']}|Working"
                                         for f, w in ORDERS.items()))
            elif msg == "GET_POSITIONS":
                rep.send_string(";".join(f"{a}|{s_}|{'Long' if q > 0 else 'Short'}|{abs(q)}|{price:.2f}"
                                         for (a, s_), q in POSITIONS.items() if q))
            elif msg.startswith("FLATTEN|"):
                acc = msg.split("|", 1)[1]
                n = sum(1 for (a, _), q in POSITIONS.items() if a == acc and q)
                for k in list(POSITIONS):
                    if k[0] == acc:
                        POSITIONS[k] = 0
                for f in [f for f, w in ORDERS.items() if w["account"] == acc]:
                    w = ORDERS.pop(f); send({**w, "msg_type": "ORDER_STATUS", "filled": 0, "price": 0, "state": "Cancelled", "error": "", "native_error": "", "timestamp": now()})
                send({"msg_type": "FLATTENED", "account": acc, "orders_cancelled": 0, "instruments_closed": n, "timestamp": now()})
                print("FLATTEN", acc); rep.send_string(f"OK|{acc}|{n}")
            elif msg.startswith("WATCH|"):
                print("WATCH", msg.split("|", 1)[1]); rep.send_string("OK|" + msg.split("|", 1)[1])
            elif msg.startswith("MUTE|"):   # gancho de pruebas: MUTE|8 (eventos perdidos durante 8 s)
                MUTE_UNTIL = time.time() + float(msg.split("|", 1)[1]); print("MUTE hasta", MUTE_UNTIL); rep.send_string("OK")
            elif msg.startswith("ORDER|"):
                rep.send_string("ERROR|unknown request" if "--old-addon" in sys.argv else handle_order(msg[6:], "5557"))
            elif msg == "PING":
                rep.send_string("PONG" if "--old-addon" in sys.argv else f"PONG|{BOOT}|{SEQ}")
            elif msg.startswith("SET_PNL|"):  # gancho de pruebas: SET_PNL|Sim102|-510
                _, acc, val = msg.split("|"); PNL[acc] = float(val); rep.send_string("OK")
            elif msg.startswith("EMIT|"):   # gancho de pruebas: EMIT|BUY|2
                _, act, q = msg.split("|")
                oid = uuid.uuid4().hex[:8]
                send({"msg_type": "EXECUTION", "account": MASTER, "action": act, "symbol": SYMBOL, "quantity": int(q),
                      "price": round(price, 2), "order_type": "MARKET", "state": "Filled", "order_id": oid,
                      "execution_id": "E" + oid, "timestamp": now()})
                apply_fill(MASTER, act, SYMBOL, int(q)); rep.send_string("OK|" + oid)
            elif msg.startswith("PENDING|"):   # gancho de pruebas: PENDING|SELL|2|STOPMARKET|19950 (stop/TP del maestro)
                _, act, q, otype, px = msg.split("|")
                oid = uuid.uuid4().hex[:8]
                send({"msg_type": "ORDER_PENDING", "account": MASTER, "action": act, "symbol": SYMBOL, "quantity": int(q),
                      "order_type": otype, "state": "Working", "order_id": oid, "filled": 0, "price": 0,
                      "limit_price": float(px) if otype.upper() == "LIMIT" else 0, "stop_price": float(px) if otype.upper().startswith("STOP") else 0,
                      "is_entry": False, "is_exit": True, "timestamp": now()})
                rep.send_string("OK|" + oid)
            elif msg.startswith("SET_MASTER|") and "--old-addon" not in sys.argv:
                new = msg.split("|", 1)[1]
                if new in ACCOUNTS or new in OFFLINE:
                    MASTER = new; print("MASTER CAMBIADA A", MASTER)
                    send({"msg_type": "HEARTBEAT", "account": MASTER, "version": ("1.2" if "--old-addon" in sys.argv else "2.2"), "boot": BOOT, "timestamp": now()})
                    rep.send_string("OK|" + new)
                else:
                    rep.send_string("ERROR|cuenta desconocida: " + new)
            elif msg == "GET_ACCOUNTS_ALL" and "--old-addon" not in sys.argv:
                rep.send_string(";".join([f"{a}|{b:.2f}|Connected|{CONNECTION}|{PNL.get(a, 0.0):.2f}|0.00" for a, b in ACCOUNTS.items()]
                                         + [f"{a}|{b:.2f}|Disconnected|{CONNECTION}|0.00|0.00" for a, b in OFFLINE.items()]))
            else:
                rep.send_string("PONG" if msg == "PING" else "ERROR|unknown request")
        elif sock is sub:
            handle_order(sub.recv_string(), "5556")
    t = time.time()
    if t >= next_price:
        price += random.uniform(-2, 2)
        send({"msg_type": "PRICE", "symbol": SYMBOL, "last": round(price, 2), "bid": round(price - 0.25, 2),
              "ask": round(price + 0.25, 2), "timestamp": now()})
        next_price = t + 0.25
    if t >= next_hb:
        send({"msg_type": "HEARTBEAT", "account": MASTER, "version": ("1.2" if "--old-addon" in sys.argv else "2.2"), "boot": BOOT, "timestamp": now()}); next_hb = t + 5
    if t >= next_emit and not manual:
        oid = uuid.uuid4().hex[:8]; action = random.choice(["BUY", "SELL"])
        send({"msg_type": "EXECUTION", "account": MASTER, "action": action, "symbol": SYMBOL, "quantity": 1,
              "price": round(price, 2), "order_type": "MARKET", "state": "Filled", "order_id": oid,
              "execution_id": "E" + oid, "timestamp": now()})
        apply_fill(MASTER, action, SYMBOL, 1)
        send({"msg_type": "POSITION", "account": MASTER, "symbol": SYMBOL,
              "market_position": "Long" if action == "BUY" else "Short", "quantity": 1, "avg_price": round(price, 2),
              "timestamp": now()})
        print("EVENTO MAESTRO PUBLICADO:", action, oid); emitted += 1
        if once and emitted >= 1:
            time.sleep(1.5); break
        next_emit = t + 5
