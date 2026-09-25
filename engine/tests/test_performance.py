from datetime import datetime

from tradepilot.services.performance_service import PerformanceService


def _svc(container=None):
    if container is None:
        return PerformanceService(None, None)
    return container.performance


def test_round_trips_partial_closes_and_flips_become_trades(container):
    p = _svc(container)
    p.now = lambda: datetime(2026, 9, 25, 10, 0)
    t0 = datetime(2026, 9, 25, 9, 30)
    # larga de 2 cerrada de una vez: 10 puntos x 2 contratos x 20 $ = 400 $; comisiones 2 $/lado x 4 lados
    assert p.note_fill("Sim102", "NQ 12-26", "BUY", 2, 20000.0, t0) is None
    t = p.note_fill("Sim102", "NQ 12-26", "SELL", 2, 20010.0, t0.replace(minute=45))
    assert t and t["side"] == "long" and t["quantity"] == 2 and t["pnl"] == 400.0 and t["commissions"] == 8.0
    assert t["day"] == "2026-09-25" and t["opened_at"].startswith("2026-09-25T09:30") and t["fills"] == 2
    # salida parcial: 3 contratos, sale 1 a +10 y 2 a +20 -> una operación de 3 con 1000 $
    p.note_fill("Sim102", "NQ 12-26", "BUY", 3, 20000.0, t0)
    assert p.note_fill("Sim102", "NQ 12-26", "SELL", 1, 20010.0, t0) is None
    t = p.note_fill("Sim102", "NQ 12-26", "SELL", 2, 20020.0, t0)
    assert t["quantity"] == 3 and t["pnl"] == 1000.0 and abs(t["exit_price"] - 20016.6667) < 1e-3
    # giro: larga 1, vende 2 -> cierra la larga (+100) y abre corta 1; la corta cierra a +100
    p.note_fill("Sim102", "NQ 12-26", "BUY", 1, 20000.0, t0)
    t = p.note_fill("Sim102", "NQ 12-26", "SELL", 2, 20005.0, t0)
    assert t["pnl"] == 100.0 and p.open_positions()[("Sim102", "NQ")]["side"] == -1
    t = p.note_fill("Sim102", "NQ 12-26", "BUYTOCOVER", 1, 20000.0, t0)
    assert t["side"] == "short" and t["pnl"] == 100.0 and ("Sim102", "NQ") not in p.open_positions()
    # otra raíz no se mezcla
    p.note_fill("Sim102", "MNQ 12-26", "SELL", 5, 20000.0, t0)
    assert p.note_fill("Sim102", "MNQ 12-26", "BUY", 5, 19990.0, t0)["pnl"] == 100.0   # 10 pts x 5 x 2 $
    trades = container.store.get_trades("2026-09-25", "2026-09-25")
    assert len(trades) == 5 and sum(x["pnl"] for x in trades) == 1700.0


def test_positions_open_before_start_are_seeded_and_close_as_trades(container):
    from tradepilot.domain.accounts import BrokerPosition
    p = _svc(container)
    p.seed_open_positions([BrokerPosition(account_id="Sim101", symbol="NQ 12-26", quantity=-2, avg_price=20100.0)])
    p.seed_open_positions([BrokerPosition(account_id="Sim101", symbol="NQ 12-26", quantity=-9, avg_price=1.0)])   # solo la primera foto
    t = p.note_fill("Sim101", "NQ 12-26", "BUYTOCOVER", 2, 20090.0, datetime(2026, 9, 25, 11, 0))
    assert t and t["side"] == "short" and t["pnl"] == 400.0 and t["opened_at"] is None


def test_month_aggregates_days_with_broker_pnl_over_trade_pnl(container):
    p = _svc(container)
    t0 = datetime(2026, 9, 3, 10, 0)
    p.note_fill("Sim102", "NQ 12-26", "BUY", 1, 20000.0, t0); p.note_fill("Sim102", "NQ 12-26", "SELL", 1, 20010.0, t0)     # +200
    p.note_fill("Sim102", "NQ 12-26", "BUY", 1, 20000.0, t0); p.note_fill("Sim102", "NQ 12-26", "SELL", 1, 19995.0, t0)     # -100
    p.note_fill("Sim103", "NQ 12-26", "BUY", 1, 20000.0, t0.replace(day=4)); p.note_fill("Sim103", "NQ 12-26", "SELL", 1, 20020.0, t0.replace(day=4))  # +400
    p.note_daily_pnl("Sim102", 95.5, datetime(2026, 9, 3, 12, 0))          # el bróker manda sobre la suma de operaciones
    container.commissions.now = lambda: datetime(2026, 9, 3, 10, 0)
    container.commissions.note_fill("Sim102", "NQ 12-26", 4)               # comisiones de esa sesión (2 $/lado), clave 2026-09-02
    m = p.month(2026, 9)
    assert m["from"] == "2026-09-01" and m["to"] == "2026-09-30" and m["accounts"] == ["Sim102", "Sim103"]
    d3 = next(d for d in m["days"] if d["day"] == "2026-09-03")
    assert d3["trades"] == 2 and d3["wins"] == 1 and d3["losses"] == 1 and d3["pnl_trades"] == 100.0
    assert d3["pnl_broker"] == 95.5 and d3["pnl"] == 95.5 and d3["best"] == 200.0 and d3["worst"] == -100.0
    assert d3["commissions"] == 8.0 and d3["contracts"] == 4 and d3["net"] == 87.5
    d4 = next(d for d in m["days"] if d["day"] == "2026-09-04")
    assert d4["pnl_broker"] is None and d4["pnl"] == 400.0 and d4["net"] == 400.0
    assert len(m["trades"]) == 3
    only = p.month(2026, 9, "Sim103")
    assert [d["day"] for d in only["days"]] == ["2026-09-04"] and only["accounts"] == ["Sim103"]
    assert p.month(2026, 8)["days"] == []


def test_daily_pnl_is_throttled_but_changes_are_kept(container):
    p = _svc(container)
    at = datetime(2026, 9, 25, 12, 0)
    p.note_daily_pnl("Sim102", 10.0, at)
    p.note_daily_pnl("Sim102", 10.0, at)
    p.note_daily_pnl("Sim102", 42.0, at)
    rows = container.store.get_daily_pnl("2026-09-25", "2026-09-25")
    assert len(rows) == 1 and rows[0]["pnl"] == 42.0
    # tras la hora de cierre empieza la sesión del día siguiente (convención CME)
    p.note_daily_pnl("Sim102", 7.0, datetime(2026, 9, 25, 18, 0))
    assert [r["pnl"] for r in container.store.get_daily_pnl("2026-09-26", "2026-09-26")] == [7.0]
    assert [r["pnl"] for r in container.store.get_daily_pnl("2026-09-25", "2026-09-25")] == [42.0]


async def test_fills_seen_by_the_replicator_feed_the_calendar(container):
    rep = container.replication
    container.bridge.health.master_account = "Sim101"
    rep.add_rule("Sim101", "Sim102")
    ts = "2026-09-25T10:00:00.0000000-05:00"
    await rep.process_master_event({"msg_type": "EXECUTION", "account": "Sim101", "action": "BUY", "symbol": "NQ 12-26", "quantity": 2,
                                    "price": 20000.0, "order_type": "MARKET", "state": "FILLED", "order_id": "E1", "execution_id": "e1", "timestamp": ts})
    await rep.process_master_event({"msg_type": "EXECUTION", "account": "Sim101", "action": "SELL", "symbol": "NQ 12-26", "quantity": 2,
                                    "price": 20015.0, "order_type": "MARKET", "state": "FILLED", "order_id": "E2", "execution_id": "e2", "is_exit": True, "timestamp": ts})
    await rep.process_master_event({"msg_type": "EXECUTION", "account": "Sim102", "action": "BUY", "symbol": "NQ 12-26", "quantity": 2,
                                    "price": 20000.25, "order_type": "MARKET", "state": "Filled", "order_id": "F1", "master_order_id": "E1", "execution_id": "f1", "timestamp": ts})
    await rep.process_master_event({"msg_type": "EXECUTION", "account": "Sim102", "action": "SELL", "symbol": "NQ 12-26", "quantity": 2,
                                    "price": 20015.0, "order_type": "MARKET", "state": "Filled", "order_id": "F2", "master_order_id": "E2", "execution_id": "f2", "timestamp": ts})
    m = container.performance.month(2026, 9)
    assert m["accounts"] == ["Sim101", "Sim102"] and len(m["trades"]) == 2
    assert {t["account_id"]: t["pnl"] for t in m["trades"]} == {"Sim101": 600.0, "Sim102": 590.0}
