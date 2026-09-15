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
    tasks = await rep.process_master_event(_event(quantity=1).model_dump(mode="json"))
    assert tasks == []
    assert rep.stats["blocked"] == 1
    types = [a.event_type for a in container.audit.recent(5)]
    assert "BLOCKED" in types


async def test_ignores_non_replicable_and_invalid(container):
    rep = container.replication
    rep.add_rule("Sim101", "Sim102")
    assert await rep.process_master_event(_event(msg_type="HEARTBEAT").model_dump(mode="json")) == []
    assert await rep.process_master_event({"garbage": True}) == []
    assert rep.stats["errors"] == 1
