"""Lo que falló el 9/11: tope de contratos superado por dos copias seguidas, stop del engine huérfano que abrió posición, y
stop dinámico que volvía a crear lo que se canceló a mano."""
import time

from tradepilot.domain.accounts import WorkingOrder
from tradepilot.domain.risk import RiskLimit
from tradepilot.domain.replication import MasterEvent


def _event(**kw):
    base = dict(msg_type="EXECUTION", account="Sim101", action="BUY", symbol="NQ DEC26", quantity=2, price=20000.0, order_type="MARKET", state="FILLED", order_id="e1", execution_id="x1")
    base.update(kw)
    return MasterEvent(**base)


async def test_second_entry_within_the_same_second_respects_the_cap(container):
    """Dos entradas de 2 en 1 s con tope 3: la segunda se bloquea aunque el bróker aún no refleje la primera."""
    rep, risk, b = container.replication, container.risk, container.bridge
    b.noise = 0
    b.fill_orders = False                          # el bróker no refleja la copia: como en vivo durante ~2 s
    await container.accounts.sync_once()
    await rep.start()
    rep.add_rule("Sim101", "Sim102")
    risk.upsert_limit(RiskLimit(account_id="Sim102", max_position_size=3))
    t1 = await rep.process_master_event(_event().model_dump(mode="json"))
    assert [t.target_account for t in t1] == ["Sim102"]
    t2 = await rep.process_master_event(_event(order_id="e2", execution_id="x2").model_dump(mode="json"))
    assert t2 == []
    last = [a for a in container.audit.recent(5) if a.event_type == "BLOCKED"]
    assert last and "con las copias en vuelo" in last[0].message


async def test_excess_over_cap_is_reduced_at_market(container):
    risk, b = container.risk, container.bridge
    b.noise = 0
    risk.upsert_limit(RiskLimit(account_id="Sim102", max_position_size=3))
    b.positions[("Sim102", "NQ DEC26")] = 4
    await container.accounts.sync_once()           # el check de riesgo corre tras la sincronización
    assert any(a.event_type == "POSITION_CAP_FIX" and "se cierran 1" in a.message for a in container.audit.recent(10))


async def test_engine_exit_not_yet_listed_is_not_forgotten_and_is_cancelled_when_flat(container):
    trade, b = container.trade, container.bridge
    b.noise = 0
    b.positions[("Sim102", "NQ DEC26")] = 2
    await container.accounts.sync_once()
    trade._exits[("Sim102", "k1")] = "NQ DEC26"
    await trade.sweep_orphans()                     # GET_ORDERS aún no la lista: antes se olvidaba aquí
    assert ("Sim102", "k1") in trade._exits
    b.working_orders.append(("Sim102", WorkingOrder(order_id="k1", action="SELL", symbol="NQ DEC26", quantity=2, order_type="STOPMARKET", stop_price=19990.0)))
    b.positions[("Sim102", "NQ DEC26")] = 0         # la posición se cerró por otro camino (stop copiado, cierre de la maestra)
    await container.accounts.sync_once()
    await trade.sweep_orphans()
    assert ("Sim102", "k1") not in trade._exits
    assert any(a.event_type == "MANUAL_CANCEL" and "huérfana" in a.message for a in container.audit.recent(10))


async def test_dynstop_does_not_recreate_a_hand_cancelled_stop(container):
    dyn, risk, b = container.dynstop, container.risk, container.bridge
    b.noise = 0
    await b.set_master("Sim101")
    b._px["NQ DEC26"] = 20000.0
    b.positions[("Sim102", "NQ DEC26")] = 2
    risk.upsert_limit(RiskLimit(account_id="Sim102", max_daily_loss=400))
    dyn.set_config(create_if_missing=True)                  # desde el 11/11 la creación va apagada por defecto
    await container.accounts.sync_once()
    dyn._seen[("Sim102", "nq dec26", "seen")] = time.monotonic() - 60   # posición abierta hace rato: pasa la gracia
    await dyn.tick()
    assert dyn.stats["created"] == 1
    key = ("Sim102", "nq dec26")
    assert key in dyn._created
    # alguien cancela el stop a mano: desaparece de las órdenes vivas y la posición sigue
    b.working_orders = [(a, o) for a, o in b.working_orders if a != "Sim102"]
    dyn._created[key] = (dyn._created[key][0], time.monotonic() - 20)
    await container.accounts.sync_once()
    dyn._applied.clear()
    await dyn.tick()
    assert dyn.stats["created"] == 1 and key in dyn._hold
    assert any(a.event_type == "DYNSTOP_HOLD" for a in container.audit.recent(10))
