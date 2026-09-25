import asyncio
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


# ---- libro de exposición (incidentes del 16/9: stops de entradas bloqueadas y cierre copiado como entrada) ----
async def _setup(container, follower_pos: int = 0, max_size: int = 3):
    rep = container.replication
    b = container.bridge
    b.health.master_account = "Sim101"
    rep.add_rule("Sim101", "Sim102")
    if follower_pos:
        b.positions[("Sim102", "MNQ 12-26")] = follower_pos
    await container.accounts.sync_once()
    from tradepilot.domain.risk import RiskLimit
    container.risk.upsert_limit(RiskLimit(account_id="Sim102", max_position_size=max_size))
    rep.settle_margin = 0.0      # en las pruebas el bróker simulado refleja el fill al instante
    return rep, b


def _types(container, n=6):
    return [a.event_type for a in container.audit.recent(n)]


async def test_exits_of_a_blocked_entry_are_blocked_and_cancels_always_pass(container):
    """16/9 12:11: la maestra añadió entradas que se bloquearon por tamaño; sus stops/TPs se copiaron y al saltar
    dejaron a la seguidora +6. Ahora un stop/TP solo se copia hasta la posición esperada de la seguidora."""
    rep, b = await _setup(container, follower_pos=-2)
    sym = "MNQ 12-26"
    # stop y TP de la posición real (-2): pasan
    await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="BUYTOCOVER", quantity=2, symbol=sym,
                                          order_type="STOPMARKET", order_id="S1").model_dump(mode="json"))
    await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="BUYTOCOVER", quantity=2, symbol=sym,
                                          order_type="LIMIT", order_id="T1").model_dump(mode="json"))
    assert [o["master_order_id"] for o in b.sent_orders] == ["S1", "T1"]
    # segunda entrada: bloqueada por tamaño (resultante 4 > 3)
    b.fill_orders = False
    t = await rep.process_master_event(_event(action="SELL", quantity=2, symbol=sym, order_id="E2", execution_id="e2",
                                              is_exit=False).model_dump(mode="json"))
    assert t == [] and "BLOCKED" in _types(container, 2)
    # sus stop y TP NO se copian: la seguidora no tiene esa posición
    for oid, typ in (("S2", "STOPMARKET"), ("T2", "LIMIT")):
        t = await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="BUYTOCOVER", quantity=2, symbol=sym,
                                                  order_type=typ, order_id=oid).model_dump(mode="json"))
        assert t == [], oid
        last = container.audit.recent(1)[0]
        assert last.event_type == "BLOCKED" and "no tiene posición que proteger" in last.message
    assert [o["master_order_id"] for o in b.sent_orders] == ["S1", "T1"]
    # una cancelación pasa siempre, aunque la seguidora esté fuera de límite (16/9: TPs sin cancelar con +6)
    b.positions[("Sim102", sym)] = 6
    await container.accounts.sync_once()
    t = await rep.process_master_event(_event(msg_type="ORDER_CANCELLED", action="BUYTOCOVER", quantity=2, symbol=sym,
                                              order_type="LIMIT", order_id="T1").model_dump(mode="json"))
    assert len(t) == 1 and b.sent_orders[-1]["msg_type"] == "ORDER_CANCELLED"


async def test_master_exit_never_opens_or_flips_the_follower(container):
    """16/9 12:14: la seguidora estaba plana (cerrada por NAKED_CLOSE) y el stop de la maestra se copió como
    una entrada nueva. Un cierre del maestro con la seguidora plana se salta; con menos posición, se recorta."""
    rep, b = await _setup(container, follower_pos=0)
    sym = "NQ 12-26"
    # con is_exit del bróker
    t = await rep.process_master_event(_event(action="BUYTOCOVER", quantity=2, symbol=sym, order_id="X1", execution_id="x1",
                                              is_exit=True).model_dump(mode="json"))
    assert t == [] and container.audit.recent(1)[0].event_type == "SKIPPED"
    # sin el campo (addon antiguo): BUYTOCOVER siempre es cierre
    t = await rep.process_master_event(_event(action="BUYTOCOVER", quantity=2, symbol=sym, order_id="X2", execution_id="x2")
                                       .model_dump(mode="json"))
    assert t == [] and "no tiene posición" in container.audit.recent(1)[0].message
    assert b.sent_orders == []
    # seguidora corta 1 (multiplicador distinto): el cierre de 2 de la maestra se recorta a 1 y nunca invierte
    b.positions[("Sim102", sym)] = -1
    await container.accounts.sync_once()
    t = await rep.process_master_event(_event(action="BUYTOCOVER", quantity=2, symbol=sym, order_id="X3", execution_id="x3",
                                              is_exit=True).model_dump(mode="json"))
    assert len(t) == 1 and b.sent_orders[-1]["quantity"] == 1 and "TRIMMED" in _types(container, 3)
    # el cierre ya enviado cuenta como en vuelo: un segundo fill parcial de la maestra no vuelve a cerrar
    t = await rep.process_master_event(_event(action="BUYTOCOVER", quantity=1, symbol=sym, order_id="X3", execution_id="x4",
                                              is_exit=True).model_dump(mode="json"))
    assert t == [] and len(b.sent_orders) == 1


async def test_inflight_entry_allows_its_stop_before_position_refresh(container):
    """El stop de una entrada llega en el mismo segundo que el fill, antes de que el bróker refresque la posición."""
    rep, b = await _setup(container, follower_pos=0)
    sym = "NQ 12-26"
    await rep.process_master_event(_event(action="BUY", quantity=1, symbol=sym, order_id="E1", execution_id="e1",
                                          is_exit=False).model_dump(mode="json"))
    assert container.accounts.position("Sim102", sym) == 0        # aún sin refrescar
    assert rep.expected_position("Sim102", sym) == 1
    t = await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="SELL", quantity=1, symbol=sym,
                                              order_type="STOPMARKET", order_id="S1").model_dump(mode="json"))
    assert len(t) == 1
    # el fill de la seguidora + el refresco de posiciones dejan de contar la copia como en vuelo (sin doble cuenta)
    await rep.process_master_event(_event(account="Sim102", action="BUY", quantity=1, symbol=sym, order_id="F1",
                                          master_order_id="E1", execution_id="f1").model_dump(mode="json"))
    await container.accounts.sync_once()
    assert container.accounts.position("Sim102", sym) == 1 and rep.expected_position("Sim102", sym) == 1
    # un segundo stop del mismo tipo para la misma posición ya no cabe
    t = await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="SELL", quantity=1, symbol=sym,
                                              order_type="STOPMARKET", order_id="S2").model_dump(mode="json"))
    assert t == [] and container.audit.recent(1)[0].event_type == "BLOCKED"
    # pero si el primero se cancela, el siguiente sí
    await rep.process_master_event(_event(msg_type="ORDER_CANCELLED", action="SELL", quantity=1, symbol=sym,
                                          order_type="STOPMARKET", order_id="S1").model_dump(mode="json"))
    t = await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="SELL", quantity=1, symbol=sym,
                                              order_type="STOPMARKET", order_id="S3").model_dump(mode="json"))
    assert len(t) == 1


async def test_rejected_change_is_not_a_naked_stop(container):
    """16/9 12:14:44: UnableToChangeOrder InvalidPrice con la orden aún WORKING no es un stop desnudo."""
    rep, b = await _setup(container, follower_pos=-2)
    base = {"msg_type": "ORDER_STATUS", "account": "Sim102", "action": "BUYTOCOVER", "symbol": "NQ 12-26", "quantity": 2,
            "order_type": "STOPMARKET", "order_id": "f1", "master_order_id": "S1", "filled": 0, "price": 0}
    await rep.process_master_event({**base, "state": "Working", "error": "UnableToChangeOrder", "native_error": "InvalidPrice"})
    await asyncio.sleep(0.05)
    assert b.flattened == []
    last = container.audit.recent(1)[0]
    assert last.event_type == "FOLLOWER_REJECTED" and "sigue viva" in last.message
    # un rechazo de verdad (la orden muere) sí cierra por seguridad
    await rep.process_master_event({**base, "state": "Rejected", "error": "OrderRejected", "native_error": "Insufficient margin"})
    await asyncio.sleep(0.05)
    assert b.flattened == ["Sim102"]


async def test_partially_filled_entry_keeps_counting_the_unfilled_part(container):
    """Entrada de 2 con 1 ejecutado (16/9 13:43 fue 4 con 2): el bróker dice +1 pero falta 1 por llenar, así que el stop
    de 2 no debe recortarse a 1. Cuando el resto se llena y se refresca, ya no queda nada en vuelo (sin doble cuenta)."""
    rep, b = await _setup(container, follower_pos=0)
    b.fill_orders = False
    sym = "NQ 12-26"
    await rep.process_master_event(_event(action="BUY", quantity=2, symbol=sym, order_id="E1", execution_id="e1",
                                          is_exit=False).model_dump(mode="json"))
    assert rep.expected_position("Sim102", sym) == 2
    await rep.process_master_event(_event(account="Sim102", action="BUY", quantity=1, symbol=sym, order_id="F1",
                                          master_order_id="E1", execution_id="f1").model_dump(mode="json"))
    b.positions[("Sim102", sym)] = 1
    await container.accounts.sync_once()
    assert container.accounts.position("Sim102", sym) == 1 and rep.expected_position("Sim102", sym) == 2
    t = await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="SELL", quantity=2, symbol=sym,
                                              order_type="STOPMARKET", order_id="S1").model_dump(mode="json"))
    assert len(t) == 1 and t[0].scaled_quantity == 2
    assert all(a.event_type != "TRIMMED" for a in container.audit.recent(3))
    await rep.process_master_event(_event(account="Sim102", action="BUY", quantity=1, symbol=sym, order_id="F2",
                                          master_order_id="E1", execution_id="f2").model_dump(mode="json"))
    b.positions[("Sim102", sym)] = 2
    await container.accounts.sync_once()
    assert rep.expected_position("Sim102", sym) == 2 and not rep._inflight


async def test_exit_fill_of_a_blocked_entry_never_flips_the_follower(container):
    """16/9 14:14: la maestra (larga 3 con stop S1 copiado) añadió otra entrada de 3 que a 173 se le bloqueó por tamaño,
    y su stop S2 también. Al saltar los dos stops a la vez, el fill de S2 se copió a 173 (ya plana por su S1) y quedó
    corta 3. Ahora el fill de un stop/TP que la seguidora no tiene copiado solo cierra lo que sus salidas vivas no cubren,
    en cualquier orden de llegada, y un cierre nunca invierte la posición."""
    sym = "MNQ 12-26"
    rep, b = await _setup(container, follower_pos=3, max_size=3)

    async def scenario(follower_first: bool, p: str):
        b.positions[("Sim102", sym)] = 3
        await container.accounts.sync_once()
        b.fill_orders = True
        await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="SELL", quantity=3, symbol=sym,
                                              order_type="STOPMARKET", order_id=p + "S1").model_dump(mode="json"))
        assert [o["master_order_id"] for o in b.sent_orders] == [p + "S1"]
        # segunda entrada de la maestra: bloqueada (6 > 3); su stop, bloqueado (no hay nada más que proteger)
        b.fill_orders = False
        t = await rep.process_master_event(_event(action="BUY", quantity=3, symbol=sym, order_id=p + "E2", execution_id=p + "e2",
                                                  is_exit=False).model_dump(mode="json"))
        assert t == [] and "BLOCKED" in _types(container, 2)
        t = await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="SELL", quantity=3, symbol=sym,
                                                  order_type="STOPMARKET", order_id=p + "S2").model_dump(mode="json"))
        assert t == [] and "no tiene posición que proteger" in container.audit.recent(1)[0].message
        follower_fill = _event(account="Sim102", action="SELL", quantity=3, symbol=sym, order_id=p + "F1",
                               master_order_id=p + "S1", execution_id=p + "f1").model_dump(mode="json")
        master_s2 = _event(action="SELL", quantity=3, symbol=sym, order_id=p + "S2", execution_id=p + "s2fill",
                           is_exit=True).model_dump(mode="json")
        master_s1 = _event(action="SELL", quantity=3, symbol=sym, order_id=p + "S1", execution_id=p + "s1fill",
                           is_exit=True).model_dump(mode="json")
        if follower_first:
            # el stop propio de la seguidora salta (queda plana, el bróker aún no lo refleja) y luego llega el fill de S2
            await rep.process_master_event(follower_fill)
            assert rep.expected_position("Sim102", sym) == 0
            t = await rep.process_master_event(master_s2)
            assert t == [] and container.audit.recent(1)[0].event_type == "SKIPPED"
            t = await rep.process_master_event(master_s1)
            assert t == [] and "ya ejecutó su orden" in container.audit.recent(1)[0].message
        else:
            # el fill de S2 llega antes que el de la seguidora: sus 3 siguen cubiertos por su copia viva de S1
            t = await rep.process_master_event(master_s2)
            assert t == [] and "ya tienen salida propia viva" in container.audit.recent(1)[0].message
            await rep.process_master_event(follower_fill)
            t = await rep.process_master_event(master_s1)
            assert t == []
        assert [o["master_order_id"] for o in b.sent_orders] == [p + "S1"], "solo se copió el stop; ningún cierre extra"
        # el bróker refresca: la seguidora está plana y nada queda en vuelo
        b.positions[("Sim102", sym)] = 0
        await container.accounts.sync_once()
        assert rep.expected_position("Sim102", sym) == 0 and not rep._inflight
        b.sent_orders.clear()

    await scenario(follower_first=True, p="a")
    await scenario(follower_first=False, p="b")


async def test_master_close_is_copied_when_the_follower_has_no_exit_of_its_own(container):
    """Lo contrario: si la seguidora tiene posición sin stop/TP propio (p. ej. igualada a mano), el fill de un stop de la
    maestra que no tiene copiado sí la cierra, hasta su posición."""
    rep, b = await _setup(container, follower_pos=2, max_size=3)
    sym = "MNQ 12-26"
    await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="SELL", quantity=3, symbol=sym,
                                          order_type="STOPMARKET", order_id="S9").model_dump(mode="json"))
    assert len(b.sent_orders) == 1       # el stop se copia (recortado a 2)
    rep._sent_pending.clear(); rep._live_exits.clear()   # como si esa copia no existiera: seguidora sin cobertura
    t = await rep.process_master_event(_event(action="SELL", quantity=3, symbol=sym, order_id="S9", execution_id="s9",
                                              is_exit=True).model_dump(mode="json"))
    assert len(t) == 1 and b.sent_orders[-1]["quantity"] == 2 and "TRIMMED" in _types(container, 3)
    # un cierre contra el signo de la posición (la seguidora ya está corta) nunca se copia
    b.positions[("Sim102", sym)] = -1
    rep._inflight.clear()
    await container.accounts.sync_once()
    t = await rep.process_master_event(_event(action="SELL", quantity=1, symbol=sym, order_id="S10", execution_id="s10",
                                              is_exit=True).model_dump(mode="json"))
    assert t == [] and "no tiene posición larga" in container.audit.recent(1)[0].message


async def test_out_of_order_seq_is_neither_restart_nor_gap(container):
    """16/9 14:11: 15957 -> 15959 -> 15958 se auditó como GAP + ADDON_RESTART. Un seq atrasado unos mensajes es reorden."""
    rep = container.replication
    for seq in (100, 101, 103, 102, 104, 104):
        await rep.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "seq": seq})
    assert rep.stats["seq_gaps"] == 0 and rep.stats["addon_restarts"] == 0 and rep.last_seq == 104
    assert not any(a.event_type in ("GAP", "ADDON_RESTART") for a in container.audit.recent(10))
    # un hueco que no se rellena en el plazo sí es una pérdida
    rep.seq_grace = 0.0
    await rep.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "seq": 107})
    assert rep.stats["seq_gaps"] == 0            # todavía dentro del plazo (se evalúa en el siguiente mensaje)
    await rep.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "seq": 108})
    assert rep.stats["seq_gaps"] == 2 and container.audit.recent(1)[0].event_type == "GAP"
    # seq que vuelve a empezar: reinicio
    await rep.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "seq": 3})
    assert rep.stats["addon_restarts"] == 1 and rep.last_seq == 3


async def test_stale_live_exit_is_purged_and_opposite_side_exits_do_not_count(container):
    """16/9 16:32-16:36: un stop de 1 fantasma en el libro de salidas recortó cada stop nuevo a 1 (las 5 seguidoras
    quedaron cortas 1 sin stop, tres veces) y bloqueó el stop de la larga siguiente. El libro se contrasta con las
    órdenes reales del bróker y una salida del lado contrario nunca cuenta."""
    import time
    rep, b = await _setup(container, follower_pos=-2)
    sym = "MNQ 12-26"
    # entrada vieja en el libro que el bróker ya no tiene (ejecutada/cancelada sin que llegara el estado)
    rep._live_exits.setdefault(("sim102", "MNQ"), {})["OLD"] = ("stop", 1, time.monotonic() - 30)
    await container.accounts.sync_once()          # GET_ORDERS del bróker: sin órdenes vivas
    await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="BUYTOCOVER", quantity=2, symbol=sym,
                                          order_type="STOPMARKET", order_id="S1").model_dump(mode="json"))
    assert b.sent_orders[-1]["quantity"] == 2 and "TRIMMED" not in _types(container, 3)
    assert "OLD" not in rep._live_exits[("sim102", "MNQ")] and "S1" in rep._live_exits[("sim102", "MNQ")]
    # la copia recién enviada aún no sale en GET_ORDERS (foto de hace un instante): no se purga
    await container.accounts.sync_once()
    assert "S1" in rep._live_exits[("sim102", "MNQ")]
    # y cuando el bróker la reporta viva, se mantiene aunque pase el plazo
    from tradepilot.domain.accounts import WorkingOrder
    b.working_orders = [("Sim102", WorkingOrder(order_id="f1", master_order_id="S1", action="BUYTOCOVER", symbol=sym, quantity=2,
                                                order_type="STOPMARKET", stop_price=20100.0))]
    rep._live_exits[("sim102", "MNQ")]["S1"] = ("stop", 2, time.monotonic() - 30)
    await container.accounts.sync_once()
    await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="BUYTOCOVER", quantity=2, symbol=sym,
                                          order_type="STOPMARKET", order_id="S2").model_dump(mode="json"))
    assert "BLOCKED" in _types(container, 2) and "S1" in rep._live_exits[("sim102", "MNQ")]
    # la seguidora pasa a larga 1: el stop BUYTOCOVER que sigue vivo en el libro no cuenta contra un stop SELL
    b.working_orders = []
    b.positions[("Sim102", sym)] = 1
    rep._inflight.clear()
    await container.accounts.sync_once()
    rep._live_exits[("sim102", "MNQ")]["S1"] = ("stop", 2, time.monotonic())
    await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="SELL", quantity=1, symbol=sym,
                                          order_type="STOPMARKET", order_id="S3").model_dump(mode="json"))
    assert b.sent_orders[-1]["master_order_id"] == "S3" and b.sent_orders[-1]["quantity"] == 1


async def test_master_partial_exit_copies_what_the_follower_still_owes(container):
    """16/9 16:34: el stop se copió por 1, el maestro salió de 2 en dos fills parciales y el engine los saltó ("ya
    ejecutó su orden"): las seguidoras quedaron cortas 1. Ahora se copia lo que falte respecto a lo ejecutado del maestro."""
    rep, b = await _setup(container, follower_pos=-2)
    sym = "MNQ 12-26"
    await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="BUYTOCOVER", quantity=2, symbol=sym,
                                          order_type="STOPMARKET", order_id="S1").model_dump(mode="json"))
    rep._sent_pending[("sim102", "S1")] = 1        # como si la copia hubiera salido recortada a 1
    # la copia (1) de la seguidora se ejecuta
    await rep.process_master_event(_event(account="Sim102", action="BUYTOCOVER", quantity=1, symbol=sym, order_id="F1",
                                          master_order_id="S1", execution_id="f1", price=20100.0).model_dump(mode="json"))
    b.sent_orders.clear()
    # primer fill parcial del maestro (1 de 2): la seguidora ya ejecutó 1 -> nada que copiar
    t = await rep.process_master_event(_event(action="BUYTOCOVER", quantity=1, symbol=sym, order_id="S1", execution_id="m1",
                                              is_exit=True, price=20100.0).model_dump(mode="json"))
    assert t == [] and "SKIPPED" in _types(container, 1) and not b.sent_orders
    # segundo fill parcial (2 de 2): a la seguidora le falta 1 -> se copia 1 y queda plana
    t = await rep.process_master_event(_event(action="BUYTOCOVER", quantity=1, symbol=sym, order_id="S1", execution_id="m2",
                                              is_exit=True, price=20100.25).model_dump(mode="json"))
    assert len(t) == 1 and b.sent_orders[-1]["quantity"] == 1 and b.sent_orders[-1]["action"] == "BUYTOCOVER"
    assert rep.expected_position("Sim102", sym) == 0


async def test_recent_fills_are_not_settled_against_a_lagging_snapshot(container):
    """16/9 16:34: la foto de posiciones (2 s por detrás) no reflejaba aún el fill y el engine lo dio por asentado: la
    posición esperada de 191 quedó en -1 con 2 reales y su stop se bloqueó. Un fill recién recibido sigue en vuelo."""
    rep, b = await _setup(container)
    rep.settle_margin = 1.0
    sym = "MNQ 12-26"
    await rep.process_master_event(_event(action="SELL", quantity=2, symbol=sym, order_id="E1", execution_id="e1").model_dump(mode="json"))
    await rep.process_master_event(_event(account="Sim102", action="SELL", quantity=2, symbol=sym, order_id="F1",
                                          master_order_id="E1", execution_id="f1").model_dump(mode="json"))
    assert rep.expected_position("Sim102", sym) == -2
    b.positions[("Sim102", sym)] = 0               # el simulador ya aplicó el fill: dejarlo como un bróker que va por detrás
    await container.accounts.sync_once()           # el bróker aún dice plana: el fill no se asienta todavía
    assert rep.expected_position("Sim102", sym) == -2
    await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="BUYTOCOVER", quantity=2, symbol=sym,
                                          order_type="STOPMARKET", order_id="S1").model_dump(mode="json"))
    assert b.sent_orders[-1]["quantity"] == 2 and "BLOCKED" not in _types(container, 2)
    # pasado el margen, con el bróker ya al día, se asienta y no se cuenta dos veces
    b.positions[("Sim102", sym)] = -2
    rep.settle_margin = 0.0
    await container.accounts.sync_once()
    assert rep.expected_position("Sim102", sym) == -2 and not rep._inflight


async def test_master_fill_of_a_pending_copy_tells_the_addon_how_far_the_copy_must_go(container):
    """17/9: el stop del maestro llenó 1 de 2 y el addon mandó "1 a mercado para seguirle" mientras el stop de la seguidora
    saltaba a la vez: doble salida. Con el addon 2.6 la copia viva se ejecuta sola; el engine le dice hasta dónde debe
    llegar (lo ejecutado del maestro, escalado) y solo reconcilia lo que falte pasado el plazo."""
    rep, b = await _setup(container, follower_pos=4, max_size=8)
    rep.update_rule(rep.rules[0].id, multiplier=2)
    sym = "MNQ 12-26"
    await rep.process_master_event(_event(msg_type="ORDER_PENDING", action="SELL", quantity=2, symbol=sym,
                                          order_type="STOPMARKET", order_id="S1").model_dump(mode="json"))
    assert b.sent_orders[-1]["quantity"] == 4 and b.sent_orders[-1]["master_filled_scaled"] is None
    b.next_replies.append("OK|EXECUTION_WAIT")
    await rep.process_master_event(_event(action="SELL", quantity=1, symbol=sym, order_id="S1", execution_id="m1",
                                          is_exit=True, price=20000.0).model_dump(mode="json"))
    assert b.sent_orders[-1]["quantity"] == 2 and b.sent_orders[-1]["master_filled_scaled"] == 2
    note = next(a for a in container.audit.recent(3) if a.event_type == "REPLICATED")
    assert "se ejecuta sola" in note.message
    await rep.process_master_event(_event(action="SELL", quantity=1, symbol=sym, order_id="S1", execution_id="m2",
                                          is_exit=True, price=20000.0).model_dump(mode="json"))
    assert b.sent_orders[-1]["quantity"] == 2 and b.sent_orders[-1]["master_filled_scaled"] == 4
    # una entrada a mercado del maestro también lo lleva (lo ejecutado de esa orden, escalado), sin estorbar
    await rep.process_master_event(_event(action="BUY", quantity=1, symbol=sym, order_id="E9", execution_id="e9").model_dump(mode="json"))
    assert b.sent_orders[-1]["master_filled_scaled"] == 2


async def test_account_locked_by_the_prop_firm_is_disabled_and_stops_receiving_copies(container):
    """17/9: cuatro cuentas APEX pasaron a "Order can be placed by administrators only" a media sesión y el engine les
    siguió mandando cada copia (y el cierre por stop rechazado, que tampoco entraba). Ahora se desactivan solas."""
    rep, b = await _setup(container)
    ts = "2026-09-17T10:00:00.0000000-05:00"
    await rep.process_master_event({"msg_type": "ORDER_STATUS", "account": "Sim102", "action": "SELL", "quantity": 2,
                                    "symbol": "NQ 12-26", "order_type": "STOPMARKET", "state": "Rejected", "order_id": "F1",
                                    "master_order_id": "S1", "error": "OrderRejected",
                                    "native_error": "Order can be placed by administrators only", "timestamp": ts})
    await asyncio.sleep(0)
    snap = container.accounts.accounts["Sim102"]
    assert snap.enabled is False and snap.enabled_source == "user"
    types = _types(container, 4)
    assert "ACCOUNT_LOCKED" in types and "NAKED_CLOSE" not in types
    n = len(b.sent_orders)
    await rep.process_master_event(_event(order_id="E2", execution_id="e2").model_dump(mode="json"))
    assert len(b.sent_orders) == n
    assert any(a.event_type == "BLOCKED" and "desactivada" in a.message for a in container.audit.recent(3))
    # un rechazo normal (margen, tamaño) no bloquea la cuenta
    await container.accounts.set_settings("Sim102", enabled=True)
    await rep.process_master_event({"msg_type": "ORDER_STATUS", "account": "Sim102", "action": "BUY", "quantity": 2,
                                    "symbol": "NQ 12-26", "order_type": "MARKET", "state": "Rejected", "order_id": "F2",
                                    "master_order_id": "E2", "error": "OrderRejected",
                                    "native_error": "Your maximum order quantity has been met", "timestamp": ts})
    await asyncio.sleep(0)
    assert container.accounts.accounts["Sim102"].enabled is True


async def test_execution_quality_records_master_price_and_each_follower_slippage(container):
    """"Calidad de ejecución": por cada orden copiada, el precio medio del maestro y el fill de cada seguidora con su
    deslizamiento en ticks (positivo = peor) y sus tiempos; un fill de una orden que no se copió (FIX/SYNC) no cuenta."""
    rep, b = await _setup(container)
    ts = "2026-09-25T10:00:00.0000000-05:00"
    await rep.process_master_event(_event(action="BUY", quantity=1, price=20000.0, order_id="E1", execution_id="e1", timestamp=ts).model_dump(mode="json"))
    await rep.process_master_event(_event(action="BUY", quantity=1, price=20001.0, order_id="E1", execution_id="e2", timestamp=ts).model_dump(mode="json"))
    await rep.process_master_event(_event(account="Sim102", action="BUY", quantity=2, price=20001.0, order_id="F1",
                                          master_order_id="E1", execution_id="f1", timestamp="2026-09-25T10:00:00.2500000-05:00").model_dump(mode="json"))
    await rep.process_master_event(_event(account="Sim102", action="BUYTOCOVER", quantity=1, price=20005.0, order_id="F9",
                                          master_order_id="FIX-abc", execution_id="f9", timestamp=ts).model_dump(mode="json"))
    recs = rep.recent_executions()
    assert len(recs) == 1
    r = recs[0]
    assert r["order_id"] == "E1" and r["quantity"] == 2 and r["price"] == 20000.5 and r["kind"] == "entry" and r["tick"] == 0.25
    f = r["followers"][0]
    assert f["name"] == "Sim102" and f["expected"] == 2 and f["filled"] == 2 and f["price"] == 20001.0
    assert f["slip"] == 0.5 and f["slip_ticks"] == 2.0 and f["broker_ms"] == 250 and f["latency_ms"] is not None
    # una venta que sale peor es un precio más bajo
    await rep.process_master_event(_event(action="SELL", quantity=2, price=20010.0, order_id="X1", execution_id="x1", is_exit=True, timestamp=ts).model_dump(mode="json"))
    await rep.process_master_event(_event(account="Sim102", action="SELL", quantity=2, price=20009.75, order_id="F2",
                                          master_order_id="X1", execution_id="f2", timestamp=ts).model_dump(mode="json"))
    r = rep.recent_executions(limit=1)[0]
    assert r["order_id"] == "X1" and r["kind"] == "exit" and r["followers"][0]["slip_ticks"] == 1.0


async def test_entry_preset_applies_to_every_rule_of_the_master(container):
    rep = container.replication
    container.bridge.health.master_account = "Sim101"
    rep.add_rule("Sim101", "Sim102")
    rep.add_rule("Sim101", "Sim103", multiplier=2)
    rep.add_rule("Otra", "Sim104")
    updated = rep.set_entry_for_all("Sim101", entry_mode="limit", tolerance_ticks=0, entry_timeout_s=2, entry_fallback="market")
    assert sorted(u.follower_account for u in updated) == ["Sim102", "Sim103"]
    assert all(r.entry_mode == "limit" and r.tolerance_ticks == 0 and r.entry_timeout_s == 2 for r in rep.rules if r.master_account == "Sim101")
    assert next(r for r in rep.rules if r.follower_account == "Sim104").entry_mode == "market"
    assert next(r for r in rep.rules if r.follower_account == "Sim103").multiplier == 2
    assert "ENTRY_MODE_SET" in _types(container, 2)
    rep.set_entry_for_all("Sim101", entry_mode="market")
    assert all(r.entry_mode == "market" for r in rep.rules)
