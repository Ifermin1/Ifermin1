"""Lo que falló el 11/11: pausas por drawdown en cuentas que el bróker reporta en 0,00; bucle de POSITION_CAP_FIX cada 20 s
contra un rechazo del bróker; stops creados a ciegas que se ejecutaron al instante; perfiles de riesgo en cuentas inactivas."""
import time

from tradepilot.domain.risk import RiskLimit


async def test_account_reported_at_zero_is_not_halted_by_drawdown_or_daily_loss(container):
    risk, b = container.risk, container.bridge
    b.noise = 0
    risk.upsert_limit(RiskLimit(account_id="Sim102", max_trailing_drawdown=2500, max_daily_loss=400))
    await container.accounts.sync_once()                    # marca de agua con el saldo real
    b.accounts["Sim102"] = 0.0                              # la plataforma deja de cargar la cuenta: vale 0,00
    await container.accounts.sync_once()
    lim = risk.limits["Sim102"]
    assert not lim.trading_halted
    assert not any(a.event_type in ("DRAWDOWN_LIMIT", "DAILY_LOSS_LIMIT") for a in container.audit.recent(20))
    assert not risk.is_live(container.accounts.accounts["Sim102"])
    # vuelve con saldo: se vigila otra vez
    b.accounts["Sim102"] = 100_000.0
    await container.accounts.sync_once()
    assert risk.is_live(container.accounts.accounts["Sim102"])


async def test_disabled_account_is_not_halted(container):
    risk, b = container.risk, container.bridge
    b.noise = 0
    risk.upsert_limit(RiskLimit(account_id="Sim102", max_daily_loss=400))
    await container.accounts.set_settings("Sim102", enabled=False)
    b.pnl["Sim102"] = -900.0
    await container.accounts.sync_once()
    assert not risk.limits["Sim102"].trading_halted


async def test_cap_fix_backs_off_when_the_broker_keeps_rejecting(container, monkeypatch):
    risk, b = container.risk, container.bridge
    b.noise = 0
    risk.upsert_limit(RiskLimit(account_id="Sim102", max_position_size=3))
    b.positions[("Sim102", "NQ DEC26")] = 4

    async def rejected(order):
        raise RuntimeError("Order quantity exceeds maximum. Limit: 4 Current: 5")
    monkeypatch.setattr(b, "manual_order", rejected)
    for _ in range(6):
        risk._cap_fixed.clear()                             # simula que pasaron los 20 s entre intentos
        await container.accounts.sync_once()
    audits = container.audit.recent(50)
    assert sum(1 for a in audits if a.event_type == "ERROR" and "exceso" in a.message) == 3
    stuck = [a for a in audits if a.event_type == "POSITION_CAP_STUCK"]
    assert len(stuck) == 1 and "No se insiste en 15 min" in stuck[0].message
    assert risk._cap_hold[("Sim102", "NQ")] > time.monotonic()
    # al bajar al tope (a mano) se olvida el bloqueo
    b.positions[("Sim102", "NQ DEC26")] = 3
    await container.accounts.sync_once()
    assert ("Sim102", "NQ") not in risk._cap_hold


async def test_dynstop_does_not_create_stops_unless_asked_and_waits_a_grace_period(container):
    dyn, risk, b = container.dynstop, container.risk, container.bridge
    b.noise = 0
    await b.set_master("Sim101")
    b._px["NQ DEC26"] = 20000.0
    b.positions[("Sim102", "NQ DEC26")] = 2                 # posición SIN stop
    risk.upsert_limit(RiskLimit(account_id="Sim102", max_daily_loss=400))
    await container.accounts.sync_once()
    assert dyn.config["create_if_missing"] is False
    await dyn.tick()
    assert not [o for a, o in b.working_orders if a == "Sim102"]
    assert "apagada" in dyn.state()["accounts"]["Sim102"]["note"]
    dyn.set_config(create_if_missing=True)
    await dyn.tick()                                        # recién vista: gracia de 20 s
    assert not [o for a, o in b.working_orders if a == "Sim102"]
    assert "recién abierta" in dyn.state()["accounts"]["Sim102"]["note"]
    dyn._seen[("Sim102", "nq dec26", "seen")] = time.monotonic() - 60
    await dyn.tick()
    created = [o for a, o in b.working_orders if a == "Sim102" and o.order_type == "STOPMARKET"]
    assert len(created) == 1 and created[0].stop_price == 19990.25    # 390 $ / (20 × 2) = 9,75 puntos


async def test_dynstop_config_from_the_old_default_is_migrated_once(container):
    from tradepilot.services.dynstop_service import CONFIG_VERSION, DynStopService
    store = container.store
    store.set_kv("dynstop.config", '{"enabled": true, "create_if_missing": true, "margin_usd": 10.0}')   # guardado por la versión anterior
    svc = DynStopService(store, container.accounts, container.risk, container.trade, container.market, container.audit, container.bridge)
    assert svc.config["create_if_missing"] is False
    svc2 = DynStopService(store, container.accounts, container.risk, container.trade, container.market, container.audit, container.bridge)
    assert svc2.config["create_if_missing"] is False
    svc2.set_config(create_if_missing=True)                 # el usuario lo enciende a sabiendas: se respeta en adelante
    svc3 = DynStopService(store, container.accounts, container.risk, container.trade, container.market, container.audit, container.bridge)
    assert svc3.config["create_if_missing"] is True
    import json
    assert json.loads(store.get_kv("dynstop.config"))["version"] == CONFIG_VERSION


async def test_auto_risk_waits_until_the_account_has_a_balance(container):
    b = container.bridge
    b.noise = 0
    b.accounts["APEX112924000009999"] = 0.0                 # aparece listada pero en 0,00
    await container.accounts.sync_once()
    assert container.risk.limits.get("APEX112924000009999") is None
    assert not any(a.event_type == "AUTO_RISK_APPLIED" for a in container.audit.recent(10))
    b.accounts["APEX112924000009999"] = 50_000.0            # la plataforma la carga
    await container.accounts.sync_once()
    lim = container.risk.limits.get("APEX112924000009999")
    assert lim is not None and lim.max_trailing_drawdown == 2500
    assert any(a.event_type == "AUTO_RISK_APPLIED" for a in container.audit.recent(10))
