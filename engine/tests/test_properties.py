"""Robustez 7: propiedades de la matemática de la copia y de las estadísticas (hypothesis)."""
from hypothesis import given, settings
from hypothesis import strategies as st

from tradepilot.domain.replication import ReplicationRule
from tradepilot.domain.risk import RiskLimit, Schedule
from tradepilot.domain.tradestats import trade_stats
from tradepilot.services.risk_service import RiskService


def _rule(mult: float) -> ReplicationRule:
    return ReplicationRule(id="r", master_account="A", follower_account="B", multiplier=mult)


@settings(max_examples=200)
@given(q=st.integers(min_value=0, max_value=500), mult=st.floats(min_value=0.01, max_value=20, allow_nan=False, allow_infinity=False))
def test_scale_is_nonnegative_monotonic_and_bounded(q, mult):
    r = _rule(mult)
    s = r.scale(q)
    assert 0 <= s <= q * mult + 1e-9
    assert r.scale(0) == 0
    assert r.scale(q + 1) >= s                       # más contratos en la maestra nunca da menos en la seguidora
    if mult >= 1:
        assert s >= q                                # con multiplicador >= 1 nunca copia menos que la maestra
    else:
        assert s <= q


@settings(max_examples=200)
@given(pos=st.integers(min_value=-20, max_value=20), q=st.integers(min_value=1, max_value=40), cap=st.integers(min_value=1, max_value=20), buy=st.booleans())
def test_position_cap_never_lets_exposure_grow_past_the_limit(pos, q, cap, buy):
    """Con tope de contratos, una orden que AUMENTA la exposición por encima del tope se rechaza; una que la reduce se permite."""
    class _Accounts:
        def __init__(self, qty):
            self.qty = qty
        def position(self, account, symbol):
            return self.qty
    risk = RiskService.__new__(RiskService)
    risk.limits = {"B": RiskLimit(account_id="B", max_position_size=cap)}
    risk.kill_switch = False
    risk.accounts = _Accounts(pos)
    risk.day_closed_day = ""
    risk.day_ack_day = ""
    risk.schedule = Schedule()
    risk.session_closed_day = ""
    ok, why = risk.allows("B", q, "NQ 12-26", "BUY" if buy else "SELL")
    resulting = pos + (q if buy else -q)
    grows = abs(resulting) > abs(pos)
    if q > cap:
        assert not ok
    elif grows and abs(resulting) > cap:
        assert not ok, why
    elif not grows:
        assert ok, why                               # reducir o cerrar siempre se permite


@settings(max_examples=150)
@given(st.lists(st.tuples(st.floats(min_value=-5000, max_value=5000, allow_nan=False), st.floats(min_value=0, max_value=50, allow_nan=False)), max_size=60))
def test_trade_stats_invariants(rows):
    trades = [{"pnl": p, "commissions": c, "closed_at": f"2026-10-20T10:{i % 60:02d}:00", "opened_at": None, "quantity": 1} for i, (p, c) in enumerate(rows)]
    s = trade_stats(trades)
    assert s["n"] == len(trades) and s["wins"] + s["losses"] <= s["n"]
    assert abs(s["net"] - round(s["gross_profit"] - s["gross_loss"], 2)) <= 0.05 * max(1, len(trades))
    if s["n"]:
        assert 0 <= s["win_rate"] <= 1
    assert s["gross_profit"] >= 0 and s["gross_loss"] >= 0
    assert s["max_loss_streak"] <= s["losses"] and s["max_win_streak"] <= s["wins"]
    if s["pf"] is not None:
        assert s["pf"] >= 0
