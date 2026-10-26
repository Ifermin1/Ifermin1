"""Robustez 8: lo que el addon v3.0 cuenta de sí mismo en el HEARTBEAT y cómo lo usa el engine."""


async def test_heartbeat_v3_fields_and_alone_audit(container):
    rep = container.replication
    h = container.bridge.health
    await rep.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "version": "3.0", "boot": "abc", "engine_silent_s": 3, "uptime_s": 120, "restarts": 0, "seq": 1})
    assert h.addon_version == "3.0" and h.addon_engine_silent_s == 3 and h.addon_uptime_s == 120 and h.addon_self_restarts == 0
    assert not [a for a in container.audit.recent(10) if a.event_type == "ADDON_ALONE"]
    # el addon dice que llevaba 5 min sin engine: se audita una vez
    await rep.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "version": "3.0", "boot": "abc", "engine_silent_s": 300, "uptime_s": 420, "restarts": 0, "seq": 2})
    await rep.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "version": "3.0", "boot": "abc", "engine_silent_s": 305, "uptime_s": 425, "restarts": 0, "seq": 3})
    alone = [a for a in container.audit.recent(10) if a.event_type == "ADDON_ALONE"]
    assert len(alone) == 1 and "5 min" in alone[0].message
    # el addon reconstruyó sus sockets: se audita el cambio
    await rep.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "version": "3.0", "boot": "abc", "engine_silent_s": 1, "uptime_s": 430, "restarts": 1, "seq": 4})
    assert any(a.event_type == "ADDON_SELF_RESTART" for a in container.audit.recent(10)) and h.addon_self_restarts == 1
    # heartbeats antiguos (sin los campos) siguen valiendo
    await rep.process_master_event({"msg_type": "HEARTBEAT", "account": "Sim101", "version": "2.9", "boot": "abc", "seq": 5})
    assert h.addon_version == "2.9"


async def test_pong_with_extra_fields_is_parsed(container):
    """PONG|boot|seq|version|silent (v3.0): el engine toma boot y seq e ignora el resto."""
    from tradepilot.infrastructure.brokers.ninja_zmq import NinjaZmqBridge
    class _B(NinjaZmqBridge):
        def __init__(self):
            self.health = container.bridge.health
        async def request(self, cmd, timeout_ms=2000):
            return "PONG|boot1|77|3.0|12"
    b = _B()
    alive, boot, seq = await b.ping_state()
    assert alive and boot == "boot1" and seq == 77
