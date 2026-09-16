from datetime import datetime

from tradepilot.domain.replication import MasterEvent, ReplicationRule


def _event(**kw):
    base = dict(msg_type="EXECUTION", account="Sim101", action="BUY", symbol="NQ 12-26", quantity=2,
                price=20000.0, order_type="MARKET", state="FILLED", order_id="abc")
    base.update(kw)
    return MasterEvent(**base)


def test_rule_matches_and_scales():
    rule = ReplicationRule(id="r1", master_account="Sim101", follower_account="Sim102", multiplier=1.5)
    assert rule.matches(_event())
    assert rule.scale(2) == 3
    assert not rule.matches(_event(account="Otro"))


def test_symbol_filter_normalized():
    rule = ReplicationRule(id="r1", master_account="Sim101", follower_account="Sim102", symbol_filter=" nq 12-26 ")
    assert rule.symbol_filter == "NQ 12-26"
    assert rule.matches(_event(symbol="NQ 12-26"))
    assert not rule.matches(_event(symbol="ES 12-26"))


def test_disabled_rule_does_not_match():
    rule = ReplicationRule(id="r1", master_account="Sim101", follower_account="Sim102", enabled=False)
    assert not rule.matches(_event())


async def test_service_replicates_and_blocks_by_kill_switch(container):
    rep = container.replication
    bridge = container.bridge
    await rep.start()
    rep.add_rule("Sim101", "Sim102", multiplier=2)
    rep.add_rule("Sim101", "Sim103", multiplier=0.4)  # 1*0.4 -> 0 => SKIPPED

    tasks = await rep.process_master_event(_event(quantity=1).model_dump(mode="json"))
    assert [t.target_account for t in tasks] == ["Sim102"]
    assert bridge.sent_orders[-1]["quantity"] == 2

    container.risk.set_kill_switch(True, "prueba")
    tasks = await rep.process_master_event(_event(quantity=1, order_id="abc2").model_dump(mode="json"))
    assert tasks == []
    assert rep.stats["blocked"] == 1
    types = [a.event_type for a in container.audit.recent(5)]
    assert "BLOCKED" in types


async def test_ignores_non_replicable_and_invalid(container):
    rep = container.replication
    rep.add_rule("Sim101", "Sim102")
    assert await rep.process_master_event(_event(msg_type="HEARTBEAT").model_dump(mode="json")) == []
    assert await rep.process_master_event({"garbage": True}) == []          # sin msg_type: se ignora
    assert await rep.process_master_event({"msg_type": "EXECUTION"}) == []  # faltan campos: error
    assert rep.stats["errors"] == 1


async def test_addon_messages_are_not_errors(container):
    rep = container.replication
    rep.add_rule("Sim101", "Sim102")
    ts = "2026-09-15T14:37:20.0725123-05:00"  # formato .NET ToString("o")
    for m in [{"msg_type": "HEARTBEAT", "account": "Sim101", "timestamp": ts},
              {"msg_type": "PRICE", "symbol": "NQ 12-26", "last": 20000.25, "bid": 20000, "ask": 20000.5, "timestamp": ts},
              {"msg_type": "POSITION", "account": "Sim101", "symbol": "NQ 12-26", "market_position": "Long",
               "quantity": 2, "avg_price": 20000.25, "timestamp": ts}]:
        assert await rep.process_master_event(m) == []
    assert rep.stats["errors"] == 0
    assert container.bridge.health.last_heartbeat is not None
    pos = container.accounts.accounts["Sim101"].open_positions
    assert pos[0].symbol == "NQ 12-26" and pos[0].quantity == 2


async def test_follower_ack_and_rejection(container):
    rep = container.replication
    rep.add_rule("Sim101", "Sim102")
    ts = "2026-09-15T14:37:20.0725123-05:00"
    # fill del follower: viene como EXECUTION con master_order_id -> no se replica
    ack = _event(account="Sim102", order_id="f1", master_order_id="m1", timestamp=ts).model_dump(mode="json")
    assert await rep.process_master_event(ack) == []
    assert rep.stats["fills"] == 1 and rep.stats["orders_out"] == 0
    # rechazo
    await rep.process_master_event({"msg_type": "ORDER_STATUS", "account": "Sim102", "action": "BUY", "symbol": "NQ 12-26",
                                    "quantity": 1, "filled": 0, "state": "Rejected", "order_id": "f1", "master_order_id": "m1",
                                    "error": "OrderRejected", "native_error": "Insufficient margin", "timestamp": ts})
    assert rep.stats["rejected"] == 1
    types = [a.event_type for a in container.audit.recent(3)]
    assert "FOLLOWER_REJECTED" in types and "FOLLOWER_FILL" in types


async def test_duplicates_and_limit_prices(container):
    rep = container.replication
    rep.add_rule("Sim101", "Sim102")
    pending = _event(msg_type="ORDER_PENDING", order_type="LIMIT", state="Working", price=19990.0,
                     limit_price=19990.0, order_id="L1").model_dump(mode="json")
    assert len(await rep.process_master_event(pending)) == 1
    assert len(await rep.process_master_event(pending)) == 0  # "Working" repetido
    assert rep.stats["duplicates"] == 1
    sent = container.bridge.sent_orders[-1]
    assert sent["msg_type"] == "ORDER_PENDING" and sent["limit_price"] == 19990.0 and sent["order_type"] == "LIMIT"


def test_bad_timestamp_does_not_drop_trade():
    for ts in ["2026-09-15T17:58:49.981841+00:000", "basura", "", None, "2026-09-15T14:37:20.0725123-05:00"]:
        e = _event(timestamp=ts)
        assert isinstance(e.timestamp, datetime)


def test_symbol_root_and_case_insensitive_master():
    rule = ReplicationRule(id="r", master_account=" sim101 ", follower_account="Sim102", symbol_filter="NQ 12-26")
    assert rule.matches(_event(account="Sim101", symbol="NQ SEP26"))   # misma raíz, distinto nombre de contrato
    assert rule.matches(_event(symbol="NQ 12-26"))
    assert not rule.matches(_event(symbol="MNQ SEP26"))                # MNQ no es NQ
    rule2 = ReplicationRule(id="r2", master_account="Sim101", follower_account="Sim102", symbol_filter="ES")
    assert rule2.matches(_event(symbol="ES DEC26")) and not rule2.matches(_event(symbol="NQ SEP26"))


async def test_no_rule_is_explained(container):
    rep = container.replication
    rep.add_rule("Sim999", "Sim102")
    await rep.process_master_event(_event(order_id="a").model_dump(mode="json"))
    last = container.audit.recent(1)[0]
    assert last.event_type == "NO_RULE" and "Sim999" in last.message and "Sim101" in last.message
    rep.add_rule("Sim101", "Sim102", symbol_filter="ES")
    await rep.process_master_event(_event(order_id="b", symbol="NQ SEP26").model_dump(mode="json"))
    last = container.audit.recent(1)[0]
    assert last.event_type == "NO_RULE" and "filtro" in last.message


async def test_pending_order_in_two_states_is_one_order(container):
    """NinjaTrader publica ORDER_PENDING en Accepted y luego en Working: debe replicarse UNA vez."""
    rep = container.replication
    rep.add_rule("Sim101", "Sim102")
    base = dict(msg_type="ORDER_PENDING", order_type="LIMIT", price=20100.0, limit_price=20100.0, order_id="TP1", action="SELL")
    assert len(await rep.process_master_event(_event(**base, state="Accepted").model_dump(mode="json"))) == 1
    assert len(await rep.process_master_event(_event(**base, state="Working").model_dump(mode="json"))) == 0
    # una modificación real (otro precio) sí pasa
    mod = dict(base, msg_type="ORDER_MODIFIED", price=20150.0, limit_price=20150.0)
    assert len(await rep.process_master_event(_event(**mod, state="ChangeSubmitted").model_dump(mode="json"))) == 1
    assert len(await rep.process_master_event(_event(**mod, state="Working").model_dump(mode="json"))) == 0
    assert rep.stats["orders_out"] == 2 and rep.stats["duplicates"] == 2


async def test_manual_fill_in_other_account_is_not_replicated(container):
    rep = container.replication
    container.bridge.health.master_account = "Sim101"
    rep.add_rule("Sim101", "Sim102")
    # cierre manual en Sim102: EXECUTION sin master_order_id desde una cuenta que no es la maestra
    tasks = await rep.process_master_event(_event(account="Sim102", order_id="manual1").model_dump(mode="json"))
    assert tasks == [] and rep.stats["orders_out"] == 0
    last = container.audit.recent(1)[0]
    assert last.event_type == "ACCOUNT_FILL" and last.target_account == "Sim102"
    # y de la maestra sí
    assert len(await rep.process_master_event(_event(order_id="m1").model_dump(mode="json"))) == 1


async def test_master_exit_fill_after_follower_already_filled_is_not_copied(container):
    """Carrera real: el stop del follower se ejecuta antes de que llegue el fill del stop del maestro."""
    rep = container.replication
    container.bridge.health.master_account = "Sim101"
    rep.add_rule("Sim101", "Sim102")
    ts = "2026-09-15T16:50:00.0000000-05:00"
    # 1. entrada a mercado del maestro
    await rep.process_master_event(_event(order_id="E1", execution_id="x1", timestamp=ts).model_dump(mode="json"))
    # 2. stop del maestro -> copiado como ORDER_PENDING al follower
    await rep.process_master_event(_event(msg_type="ORDER_PENDING", order_type="STOPMARKET", action="SELL", quantity=2,
                                          stop_price=19900.0, price=19900.0, order_id="S1", timestamp=ts).model_dump(mode="json"))
    sent_before = len(container.bridge.sent_orders)
    # 3. el follower ejecuta SU stop primero (EXECUTION con master_order_id=S1)
    await rep.process_master_event(_event(account="Sim102", action="SELL", quantity=2, price=19899.5, order_id="F1",
                                          master_order_id="S1", execution_id="f1", timestamp=ts).model_dump(mode="json"))
    # 4. ahora llega el fill del stop del maestro: NO debe copiarse
    tasks = await rep.process_master_event(_event(action="SELL", quantity=2, price=19900.0, order_id="S1",
                                                  execution_id="m1", timestamp=ts).model_dump(mode="json"))
    assert tasks == [] and len(container.bridge.sent_orders) == sent_before
    assert any(a.event_type == "SKIPPED" and "ya ejecutó" in a.message for a in container.audit.recent(3))


async def test_latency_and_slippage_measured(container):
    rep = container.replication
    container.bridge.health.master_account = "Sim101"
    rep.add_rule("Sim101", "Sim102")
    await rep.process_master_event(_event(order_id="M1", price=20000.0, action="BUY", execution_id="a").model_dump(mode="json"))
    await rep.process_master_event(_event(account="Sim102", order_id="F9", master_order_id="M1", price=20000.5, action="BUY",
                                          execution_id="b").model_dump(mode="json"))
    fill = container.audit.recent(1)[0]
    assert fill.event_type == "FOLLOWER_FILL" and fill.details["slippage"] == 0.5 and fill.details["latency_ms"] >= 0
    assert rep.stats["slippage_avg"] == 0.5 and rep.stats["latency_ms_avg"] is not None
    assert "maestro 20000.0" in fill.message


def test_symbol_mapping():
    rule = ReplicationRule(id="r", master_account="Sim101", follower_account="Sim102", target_root=" mnq ")
    assert rule.target_root == "MNQ" and rule.map_symbol("NQ DEC26") == "MNQ DEC26" and rule.map_root("NQ") == "MNQ"
    assert ReplicationRule(id="r", master_account="A", follower_account="B", target_root="").target_root is None


async def test_limit_entry_only_for_exposure_increasing_copies(container):
    rep = container.replication
    b = container.bridge
    container.bridge.health.master_account = "Sim101"
    rep.add_rule("Sim101", "Sim102", entry_mode="limit", tolerance_ticks=3, entry_timeout_s=4, entry_fallback="cancel",
                 target_root="MNQ")
    # entrada (seguidora plana): límite con tolerancia y símbolo mapeado
    await rep.process_master_event(_event(order_id="e1", execution_id="x1", symbol="NQ DEC26").model_dump(mode="json"))
    sent = b.sent_orders[-1]
    assert sent["symbol"] == "MNQ DEC26" and sent["entry_mode"] == "limit" and sent["tolerance_ticks"] == 3 \
        and sent["entry_timeout_s"] == 4 and sent["entry_fallback"] == "cancel"
    # la seguidora ya está larga 2 en MNQ (mock aplica el fill): la salida va a mercado, sin entry_mode
    assert b.positions[("Sim102", "MNQ DEC26")] == 2
    await container.accounts.sync_once()
    await rep.process_master_event(_event(order_id="e2", execution_id="x2", action="SELL", symbol="NQ DEC26").model_dump(mode="json"))
    assert "entry_mode" not in b.sent_orders[-1]
    assert "límite ±3 ticks" in [a.message for a in container.audit.recent(6) if a.event_type == "REPLICATED"][-1]


async def test_sync_expected_uses_mapped_root(container):
    rep = container.replication
    b = container.bridge
    b.health.master_account = "Sim101"
    rep.add_rule("Sim101", "Sim102", multiplier=10, target_root="MNQ")
    b.positions[("Sim101", "NQ DEC26")] = 1
    b.positions[("Sim102", "MNQ DEC26")] = 10
    await container.accounts.sync_once()
    assert container.sync.expected_positions("Sim102") == {"MNQ": 10} and container.sync.diff("Sim102") == {}


async def test_entry_missed_is_audited(container):
    await container.replication.process_master_event({"msg_type": "ENTRY_MISSED", "account": "Sim102", "action": "BUY",
                                                       "quantity": 2, "symbol": "MNQ DEC26", "master_order_id": "m1"})
    assert container.audit.recent(1)[0].event_type == "ENTRY_MISSED"
