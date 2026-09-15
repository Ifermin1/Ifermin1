"""NinjaTrader falso para probar ENGINE_MODE=ninja sin NinjaTrader.

Imita el addon TradePilotXBridge:
  PUB 5555  eventos del maestro (EXECUTION / ORDER_*), HEARTBEAT, PRICE, POSITION
            y los ACK de los followers (ORDER_STATUS + EXECUTION con master_order_id)
  SUB 5556  recibe las órdenes que el engine manda al ejecutor
  REP 5557  responde a "GET_ACCOUNTS" con "Sim101|50000.00;Sim102|25000.00"

Uso:  python scripts/fake_ninja.py            (una operación del maestro cada 5 s)
      python scripts/fake_ninja.py --once     (una y termina)
      python scripts/fake_ninja.py --reject   (los followers rechazan las órdenes)
"""
import json
import random
import sys
import time
import uuid
from datetime import datetime

import zmq

ACCOUNTS = {"Sim101": 50000.0, "Sim102": 25000.0}
MASTER = "Sim101"
SYMBOL = "NQ 12-26"


def now() -> str:
    iso = datetime.now().astimezone().isoformat(timespec="microseconds")  # 2026-09-15T17:58:49.981841+00:00
    return iso[:26] + "0" + iso[26:]  # 7 decimales antes del offset, como .NET ToString("o")


ctx = zmq.Context()
pub = ctx.socket(zmq.PUB); pub.bind("tcp://*:5555")
sub = ctx.socket(zmq.SUB); sub.bind("tcp://*:5556"); sub.setsockopt_string(zmq.SUBSCRIBE, "")
rep = ctx.socket(zmq.REP); rep.bind("tcp://*:5557")
poller = zmq.Poller(); poller.register(rep, zmq.POLLIN); poller.register(sub, zmq.POLLIN)
print("fake NinjaTrader escuchando en 5555/5556/5557 (Ctrl+C para salir)")

once, reject = "--once" in sys.argv, "--reject" in sys.argv
price = 20000.0
next_emit, next_hb, next_price, emitted = time.time() + 2, time.time() + 5, time.time() + 0.25, 0


def send(obj: dict) -> None:
    pub.send_string(json.dumps(obj))


while True:
    for sock, _ in poller.poll(100):
        if sock is rep:
            msg = rep.recv_string()
            rep.send_string(";".join(f"{a}|{b:.2f}" for a, b in ACCOUNTS.items()) if msg == "GET_ACCOUNTS" else "PONG" if msg == "PING" else "ERROR|unknown request")
        elif sock is sub:
            raw = sub.recv_string(); o = json.loads(raw)
            print("ORDEN RECIBIDA DEL ENGINE:", raw)
            fid = "F" + uuid.uuid4().hex[:6]
            base = {"account": o["account"], "action": o["action"], "symbol": o["symbol"], "quantity": o["quantity"],
                    "order_type": o["order_type"], "order_id": fid, "master_order_id": o["master_order_id"], "timestamp": now()}
            if reject:
                send({**base, "msg_type": "ORDER_STATUS", "filled": 0, "price": 0, "limit_price": 0, "stop_price": 0,
                      "state": "Rejected", "error": "OrderRejected", "native_error": "Insufficient margin"})
            else:
                send({**base, "msg_type": "ORDER_STATUS", "filled": o["quantity"], "price": price, "limit_price": 0,
                      "stop_price": 0, "state": "Filled", "error": "", "native_error": ""})
                send({**base, "msg_type": "EXECUTION", "price": price, "state": "Filled", "execution_id": "E" + fid})
    t = time.time()
    if t >= next_price:
        price += random.uniform(-2, 2)
        send({"msg_type": "PRICE", "symbol": SYMBOL, "last": round(price, 2), "bid": round(price - 0.25, 2),
              "ask": round(price + 0.25, 2), "timestamp": now()})
        next_price = t + 0.25
    if t >= next_hb:
        send({"msg_type": "HEARTBEAT", "account": MASTER, "timestamp": now()}); next_hb = t + 5
    if t >= next_emit:
        oid = uuid.uuid4().hex[:8]; action = random.choice(["BUY", "SELL"])
        send({"msg_type": "EXECUTION", "account": MASTER, "action": action, "symbol": SYMBOL, "quantity": 1,
              "price": round(price, 2), "order_type": "MARKET", "state": "Filled", "order_id": oid,
              "execution_id": "E" + oid, "timestamp": now()})
        send({"msg_type": "POSITION", "account": MASTER, "symbol": SYMBOL,
              "market_position": "Long" if action == "BUY" else "Short", "quantity": 1, "avg_price": round(price, 2),
              "timestamp": now()})
        print("EVENTO MAESTRO PUBLICADO:", action, oid); emitted += 1
        if once and emitted >= 1:
            time.sleep(1.5); break
        next_emit = t + 5
