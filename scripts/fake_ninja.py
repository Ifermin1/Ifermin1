"""NinjaTrader falso para probar ENGINE_MODE=ninja sin NinjaTrader.

Reproduce el protocolo del addon ZMQ:
  PUB 5555  publica eventos del maestro (JSON)
  SUB 5556  recibe las órdenes que el engine manda al ejecutor
  REP 5557  responde a "GET_ACCOUNTS" con "Sim101|50000;Sim102|25000"

Uso:  python scripts/fake_ninja.py            (emite una operación cada 5 s)
      python scripts/fake_ninja.py --once     (emite una y termina)
"""
import json
import sys
import time
import uuid

import zmq

ACCOUNTS = {"Sim101": 50000.0, "Sim102": 25000.0}

ctx = zmq.Context()
pub = ctx.socket(zmq.PUB); pub.bind("tcp://*:5555")
sub = ctx.socket(zmq.SUB); sub.bind("tcp://*:5556"); sub.setsockopt_string(zmq.SUBSCRIBE, "")
rep = ctx.socket(zmq.REP); rep.bind("tcp://*:5557")
poller = zmq.Poller(); poller.register(rep, zmq.POLLIN); poller.register(sub, zmq.POLLIN)
print("fake NinjaTrader escuchando en 5555/5556/5557 (Ctrl+C para salir)")

once = "--once" in sys.argv
next_emit = time.time() + 2
while True:
    for sock, _ in poller.poll(200):
        if sock is rep:
            msg = rep.recv_string()
            rep.send_string(";".join(f"{a}|{b}" for a, b in ACCOUNTS.items()) if msg == "GET_ACCOUNTS" else "")
        elif sock is sub:
            print("ORDEN RECIBIDA DEL ENGINE:", sub.recv_string())
    if time.time() >= next_emit:
        ev = {"msg_type": "EXECUTION", "account": "Sim101", "action": "BUY", "symbol": "NQ 12-26", "quantity": 1,
              "price": 20000.25, "order_type": "MARKET", "state": "FILLED", "order_id": uuid.uuid4().hex[:8]}
        pub.send_string(json.dumps(ev)); print("EVENTO MAESTRO PUBLICADO:", ev["order_id"])
        if once: time.sleep(1); break
        next_emit = time.time() + 5
