"""Catálogo de prop firms (mismo contenido que web/src/lib/propfirms.ts) para que el engine pueda aplicar un perfil de riesgo
solo, sin pasar por la consola: al aparecer una cuenta nueva (riesgo automático) o al pedirlo desde Riesgo.

Los importes son los publicados por cada firma y cambian con el tiempo: todo queda editable en Riesgo después."""
import re

# dd: "intraday" (trailing con el flotante), "eod" (cierre del día), "static" (suelo fijo); lock: dónde se bloquea el suelo
PLAN_SIZE = tuple[int, int, int, int, int]   # tamaño, drawdown, objetivo, contratos, pérdida diaria


def _sz(size: int, dd: int, target: int, contracts: int, daily: int = 0) -> PLAN_SIZE:
    return size, dd, target, contracts, daily


FIRMS: dict[str, dict] = {
    "apex": {"name": "Apex Trader Funding", "plans": {
        "eval": {"name": "Evaluación (trailing)", "dd": "intraday", "lock": "none",
                 "sizes": [_sz(25000, 1500, 1500, 4), _sz(50000, 2500, 3000, 10), _sz(75000, 2750, 4250, 12), _sz(100000, 3000, 6000, 14), _sz(150000, 5000, 9000, 17), _sz(250000, 6500, 15000, 27), _sz(300000, 7500, 20000, 35)]},
        "pa": {"name": "PA (financiada)", "dd": "intraday", "lock": "start+100",
               "sizes": [_sz(25000, 1500, 0, 4), _sz(50000, 2500, 0, 10), _sz(75000, 2750, 0, 12), _sz(100000, 3000, 0, 14), _sz(150000, 5000, 0, 17), _sz(250000, 6500, 0, 27), _sz(300000, 7500, 0, 35)]},
        "static": {"name": "Static (evaluación)", "dd": "static", "lock": "none", "sizes": [_sz(100000, 625, 2000, 2)]},
        "pa_static": {"name": "PA Static (financiada)", "dd": "static", "lock": "none", "sizes": [_sz(100000, 625, 0, 2)]},
    }},
    "topstep": {"name": "Topstep", "plans": {
        "combine": {"name": "Trading Combine", "dd": "eod", "lock": "none", "sizes": [_sz(50000, 2000, 3000, 5, 1000), _sz(100000, 3000, 6000, 10, 2000), _sz(150000, 4500, 9000, 15, 3000)]},
        "xfa": {"name": "Express Funded / Live", "dd": "eod", "lock": "start", "sizes": [_sz(50000, 2000, 0, 5, 1000), _sz(100000, 3000, 0, 10, 2000), _sz(150000, 4500, 0, 15, 3000)]},
    }},
    "mff": {"name": "MyFundedFutures", "plans": {
        "starter": {"name": "Starter (EOD)", "dd": "eod", "lock": "none", "sizes": [_sz(50000, 2000, 3000, 3), _sz(100000, 3500, 6000, 6), _sz(150000, 5000, 9000, 9)]},
        "expert": {"name": "Expert (intradía)", "dd": "intraday", "lock": "none", "sizes": [_sz(50000, 2000, 4000, 5), _sz(100000, 3000, 8000, 10), _sz(150000, 4500, 12000, 15)]},
    }},
    "tpt": {"name": "Take Profit Trader", "plans": {
        "test": {"name": "Test (EOD)", "dd": "eod", "lock": "none", "sizes": [_sz(25000, 1500, 1500, 3), _sz(50000, 2000, 3000, 6), _sz(75000, 2500, 4500, 9), _sz(100000, 3000, 6000, 12), _sz(150000, 4500, 9000, 15)]},
        "pro": {"name": "PRO (financiada)", "dd": "eod", "lock": "none", "sizes": [_sz(25000, 1500, 0, 3), _sz(50000, 2000, 0, 6), _sz(75000, 2500, 0, 9), _sz(100000, 3000, 0, 12), _sz(150000, 4500, 0, 15)]},
    }},
}

# cómo reconocer la firma por el nombre de la cuenta en NinjaTrader
ID_PATTERNS = (("apex", r"^APEX"), ("topstep", r"^(TST|TOPSTEP|XFA)"), ("mff", r"^(MFF|MYFUNDED)"), ("tpt", r"^TPT"))


def firm_from_account(account_id: str) -> str | None:
    for firm, pat in ID_PATTERNS:
        if re.match(pat, account_id.upper()):
            return firm
    return None


def guess_size(sizes: list[PLAN_SIZE], balance: float) -> PLAN_SIZE | None:
    """Tamaño del plan a partir del saldo: el más cercano por debajo o igual a 1,3 × saldo… en la práctica, el saldo inicial
    de una evaluación es exactamente el tamaño, y una cuenta con beneficio queda algo por encima."""
    if balance <= 0:
        return None
    cands = [s for s in sizes if s[0] * 0.85 <= balance <= s[0] * 1.3]
    if not cands:
        return None
    return min(cands, key=lambda s: abs(s[0] - balance))


def profile_for(firm: str, plan: str, size: int) -> dict | None:
    f = FIRMS.get(firm)
    p = f["plans"].get(plan) if f else None
    if p is None:
        return None
    row = next((s for s in p["sizes"] if s[0] == size), None)
    if row is None:
        return None
    _, dd, target, contracts, daily = row
    floor_cap = (size - dd) if p["dd"] == "static" else size if p["lock"] == "start" else (size + 100) if p["lock"] == "start+100" else 0
    return {"firm": firm, "firm_name": f["name"], "plan": plan, "plan_name": p["name"], "size": size, "drawdown": dd, "drawdown_mode": p["dd"],
            "floor_cap": floor_cap, "target": target, "contracts": contracts, "daily_loss": daily}


def infer_profile(account_id: str, balance: float, default_firm: str = "apex", default_plan: str = "eval") -> dict | None:
    """Perfil más probable para una cuenta recién vista: firma por el nombre (o la de defecto), plan por defecto, tamaño por el saldo."""
    firm = firm_from_account(account_id) or default_firm
    f = FIRMS.get(firm)
    if f is None:
        return None
    plan = default_plan if default_plan in f["plans"] else next(iter(f["plans"]))
    row = guess_size(f["plans"][plan]["sizes"], balance)
    if row is None:
        return None
    return profile_for(firm, plan, row[0])
