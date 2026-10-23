import asyncio

from httpx import AsyncClient

from tradepilot.services.ai_service import AIService
from tradepilot.services.market_service import MarketService


def test_market_bars_atr_and_volume():
    m = MarketService()
    t0 = 1_800_000_000.0   # múltiplo de 60
    # 6 minutos de velas: rango de 4 puntos por minuto, volumen creciente en el último
    for i in range(6):
        for px, vol in ((100 + i, 10), (104 + i, 10), (98 + i, 10), (102 + i, 10 if i < 5 else 100)):
            m.on_tick("NQ 12-26", px, vol, t0 + i * 60 + 10)
    m.on_tick("NQ 12-26", 110, 1, t0 + 6 * 60 + 5)   # vela actual (abierta)
    assert m.price("NQ") == 110 and len(m.recent_bars("NQ 12-26")) == 7
    atr = m.atr("NQ", 14)
    assert atr is not None and 5.5 <= atr <= 6.5          # rango de 6 puntos por vela
    assert round(m.atr_ticks("NQ"), 1) == round(atr / 0.25, 1)
    assert m.volume_ratio("NQ") > 2.5                      # último minuto cerrado con 130 frente a 40 de media
    assert m.swing("NQ", 10) == (110, 98)
    assert m.snapshot("NQ 12-26")["bars"] == 7


async def _open_long(client: AsyncClient, container, account="Sim101", qty=1, sl=40):
    r = await client.post("/api/trade/order", json={"client_id": f"tr-{account}-{qty}", "account": account, "symbol": "NQ 12-26", "action": "BUY", "quantity": qty, "order_type": "MARKET", "sl_ticks": sl})
    assert r.status_code == 200, r.text
    await container.bridge.emit_price("NQ 12-26", move=0.0)        # precio en vivo para el trailing
    await asyncio.sleep(0.02)
    await container.accounts.sync_once()
    return r.json()


def _ready(tr):
    """Como si hubieran pasado 10 s desde el último movimiento (sin el límite de 2 s ni la protección de instantánea reciente)."""
    import time
    tr.last_move_at = time.monotonic() - 10


async def test_fixed_trailing_moves_stop_only_in_favor_and_ends_when_flat(client: AsyncClient, container):
    await client.put("/api/accounts/Sim102/link", json={"master_account": "Sim101", "multiplier": 1})
    await _open_long(client, container)
    px0 = container.bridge._px["NQ 12-26"]
    r = await client.post("/api/trade/trail", json={"account": "Sim101", "symbol": "NQ 12-26", "mode": "fixed", "distance_ticks": 20, "step_ticks": 2})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["mode"] == "fixed" and t["stop_order_id"] and t["stop_price"] == px0 - 5 and t["moves"] == 1   # al activarse ya acerca el stop a 20 ticks del precio
    # el precio sube 20 puntos: el stop sigue a 20 ticks (5 pts) del mejor precio
    await container.bridge.emit_price("NQ 12-26", move=20.0)
    await asyncio.sleep(0.05)
    _ready(container.trail.trails[("sim101", "nq 12-26")])
    await container.trail.tick()
    tr = container.trail.get("Sim101", "NQ 12-26")
    best = container.bridge._px["NQ 12-26"]
    assert tr.stop_price == best - 5 and tr.moves == 2
    await asyncio.sleep(0.05)
    assert any(o["account"] == "Sim102" and o["msg_type"] == "ORDER_MODIFIED" and o["stop_price"] == best - 5 for o in container.bridge.sent_orders)   # la copia sigue
    # el precio baja 3 puntos: el stop no retrocede
    await container.bridge.emit_price("NQ 12-26", move=-3.0)
    await asyncio.sleep(0.05)
    await container.accounts.sync_once()
    _ready(tr)
    await container.trail.tick()
    assert tr.stop_price == best - 5 and tr.moves == 2
    # sube 0,25: mejora de 1 tick < step 2: no se manda
    await container.bridge.emit_price("NQ 12-26", move=3.25)
    await asyncio.sleep(0.05)
    await container.accounts.sync_once()
    _ready(tr)
    await container.trail.tick()
    assert tr.moves == 2 and "siguiente a" in tr.note
    st = (await client.get("/api/trade/trails")).json()
    assert len(st["trails"]) == 1 and st["trails"][0]["active"] is True and st["market"]["NQ 12-26"]["last"] == container.bridge._px["NQ 12-26"]
    # la posición se cierra: el trailing termina solo
    container.bridge.positions[("Sim101", "NQ 12-26")] = 0
    await container.accounts.sync_once()
    await container.trail.tick()
    assert container.trail.get("Sim101", "NQ 12-26") is None
    types = [a.event_type for a in container.audit.recent(30)]
    assert "TRAIL_START" in types and "TRAIL_MOVE" in types and "TRAIL_END" in types
    assert (await client.delete("/api/trade/trail?account=Sim101&symbol=NQ%2012-26")).status_code == 404


async def test_adaptive_trailing_uses_atr_volume_activation_and_breakeven(client: AsyncClient, container):
    await _open_long(client, container, account="Sim103", qty=2, sl=0)       # sin stop automático: el trailing lo crea
    m = container.market
    import time
    now = time.time()
    base = container.bridge._px["NQ 12-26"]
    for i in range(8):                                                       # 8 velas cerradas de 8 puntos de rango (32 ticks de ATR)
        for px in (base, base + 4, base - 4, base):
            m.on_tick("NQ 12-26", px, 10, now - (9 - i) * 60)
    r = await client.post("/api/trade/trail", json={"account": "Sim103", "symbol": "NQ 12-26", "mode": "adaptive", "distance_ticks": 40, "atr_k": 1.0, "min_ticks": 8, "max_ticks": 100,
                                                    "activate_ticks": 20, "lock_be_ticks": 60, "step_ticks": 1})
    assert r.status_code == 200, r.text
    tr = container.trail.get("Sim103", "NQ 12-26")
    assert tr.active is False and "esperando" in tr.note                     # aún sin +20 ticks
    await container.bridge.emit_price("NQ 12-26", move=8.0)                  # +32 ticks: se activa; distancia ≈ 1 × ATR ≈ 32-34 ticks
    await asyncio.sleep(0.05)
    await container.accounts.sync_once()
    _ready(tr)
    await container.trail.tick()
    assert tr.active and tr.stop_order_id and 30 <= tr.distance_now <= 36 and tr.moves == 1
    await container.accounts.sync_once()
    acc = next(a for a in container.accounts.all() if a.account_id == "Sim103")
    assert [o.order_type for o in acc.working_orders] == ["STOPMARKET"] and acc.working_orders[0].quantity == 2
    await container.bridge.emit_price("NQ 12-26", move=8.0)                  # +64 ticks: breakeven asegurado manda si está más cerca
    await asyncio.sleep(0.05)
    await container.accounts.sync_once()
    _ready(tr)
    await container.trail.tick()
    assert tr.stop_price >= acc.open_positions[0].avg_price + 0.25 and tr.moves == 2


async def test_ai_trailing_is_clamped_and_falls_back(client: AsyncClient, container):
    calls = []

    class FakeAI(AIService):
        def __init__(self):
            super().__init__(api_key="sk-test", model="claude-test")

        async def ask(self, system, prompt, model=None, max_tokens=None):
            calls.append(prompt)
            assert "Velas de 1 minuto" in prompt and "LARGO" in prompt
            return {"stop_price": self.next_stop, "reason": "bajo el mínimo del swing", "confidence": 0.8}
    ai = FakeAI()
    container.trail.ai = ai
    await _open_long(client, container, account="Sim102", qty=1, sl=40)
    px = container.bridge._px["NQ 12-26"]
    ai.next_stop = px - 2.0                                                   # propuesta más cerca que el adaptativo/fijo (40 ticks = 10 pts)
    r = await client.post("/api/trade/trail", json={"account": "Sim102", "symbol": "NQ 12-26", "mode": "ai", "distance_ticks": 40, "min_ticks": 4, "step_ticks": 1})
    assert r.status_code == 200, r.text
    tr = container.trail.get("Sim102", "NQ 12-26")
    await asyncio.sleep(0.05)
    await container.accounts.sync_once()
    _ready(tr)
    await container.trail.tick()
    assert len(calls) == 1 and tr.ai_stop == px - 2.0 and tr.stop_price == px - 2.0
    assert any(a.event_type == "TRAIL_MOVE" and "IA: bajo el mínimo" in a.message for a in container.audit.recent(10))
    # la IA pide un stop por encima del precio: se acota a min_ticks por debajo del último
    ai.next_stop = px + 5
    tr.last_ai_at = None
    await container.accounts.sync_once()
    _ready(tr)
    await container.trail.tick()
    assert tr.stop_price == px - 1.0                                          # 4 ticks = 1 punto
    # sin clave: no se puede activar el modo IA
    container.trail.ai = AIService()
    r = await client.post("/api/trade/trail", json={"account": "Sim102", "symbol": "NQ 12-26", "mode": "ai"})
    assert r.status_code == 422 and "ANTHROPIC_API_KEY" in r.text
