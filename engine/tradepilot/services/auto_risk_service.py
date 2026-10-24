"""Riesgo automático: que ninguna cuenta opere sin red.

Cuando aparece una cuenta nueva (o se activa una que estaba oculta) y no tiene límites de riesgo ni perfil de prop firm,
el engine deduce la firma por el nombre (APEX…), el tamaño por el saldo y aplica el perfil del catálogo: drawdown y su
modo (dinámico/EOD/estático), suelo bloqueado si procede, colchón, objetivo de la evaluación, pérdida diaria del plan si
la tiene y un tope de contratos (el menor entre el del plan y AUTO_RISK_MAX_CONTRACTS). Todo queda editable en Riesgo y
se audita como AUTO_RISK_APPLIED. Se puede lanzar también a mano para todas las cuentas sin límite.
"""
from loguru import logger

from tradepilot.domain.propfirms import firm_from_account, infer_profile, profile_for
from tradepilot.domain.risk import RiskLimit


class AutoRiskService:
    def __init__(self, risk, accounts, audit, settings) -> None:
        self.risk, self.accounts, self.audit, self.settings = risk, accounts, audit, settings
        self.applied: list[dict] = []

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "AUTO_RISK", True))

    def needs(self, account_id: str) -> bool:
        snap = self.accounts.find(account_id)
        if snap is None or not snap.enabled:
            return False
        lim = self.risk.limits.get(snap.account_id)
        has_limits = lim is not None and (lim.max_trailing_drawdown > 0 or lim.max_daily_loss > 0 or lim.max_position_size > 0)
        return not has_limits and not (snap.firm or "")

    async def apply(self, account_id: str, force: bool = False, firm: str | None = None, plan: str | None = None, size: int | None = None) -> dict | None:
        snap = self.accounts.find(account_id)
        if snap is None:
            raise ValueError(f"cuenta desconocida: {account_id}")
        if not force and not self.needs(snap.account_id):
            return None
        if firm and plan and size:
            prof = profile_for(firm, plan, int(size))
        else:
            prof = infer_profile(snap.account_id, snap.balance, self.settings.AUTO_RISK_FIRM, self.settings.AUTO_RISK_PLAN)
        if prof is None:
            logger.info(f"Riesgo automático: no se pudo deducir el plan de {snap.account_id} (saldo {snap.balance:,.0f})")
            return None
        contracts = min(prof["contracts"], int(self.settings.AUTO_RISK_MAX_CONTRACTS)) if self.settings.AUTO_RISK_MAX_CONTRACTS > 0 else prof["contracts"]
        prev = self.risk.limits.get(snap.account_id)
        limit = RiskLimit(account_id=snap.account_id, max_daily_loss=float(prof["daily_loss"] or 0), max_daily_profit=prev.max_daily_profit if prev else 0.0,
                          profit_goal=float(prof["target"] or 0), start_balance=float(prof["size"]), max_position_size=contracts,
                          max_trailing_drawdown=float(prof["drawdown"]), drawdown_mode=prof["drawdown_mode"], drawdown_floor_cap=float(prof["floor_cap"]),
                          drawdown_buffer=float(self.settings.AUTO_RISK_BUFFER))
        self.risk.upsert_limit(limit)
        await self.accounts.set_settings(snap.account_id, firm=prof["firm"], plan=prof["plan"], plan_size=float(prof["size"]))
        info = {"account_id": snap.account_id, "firm": prof["firm_name"], "plan": prof["plan_name"], "size": prof["size"], "drawdown": prof["drawdown"],
                "drawdown_mode": prof["drawdown_mode"], "buffer": self.settings.AUTO_RISK_BUFFER, "contracts": contracts, "target": prof["target"], "daily_loss": prof["daily_loss"]}
        self.applied.append(info)
        self.audit.log("AUTO_RISK_APPLIED", f"{snap.account_id}: perfil {prof['firm_name']} {prof['size'] // 1000}K · {prof['plan_name']} aplicado solo: drawdown "
                       f"{prof['drawdown']:,} ({prof['drawdown_mode']}), colchón {self.settings.AUTO_RISK_BUFFER:,.0f} $, máximo {contracts} contratos"
                       + (f", objetivo +{prof['target']:,}" if prof["target"] else "") + (f", pérdida diaria {prof['daily_loss']:,}" if prof["daily_loss"] else "")
                       + ". Revísalo en Riesgo si el tamaño no es ese.", target=snap.account_id, details=info)
        return info

    async def on_account_seen(self, account_id: str) -> None:
        """Llamado por AccountService al descubrir o activar una cuenta. Solo actúa solo cuando el nombre delata la firma
        (APEX…, TST…): a una cuenta Sim o de nombre desconocido no se le inventa un plan; para esas está el botón de Riesgo."""
        if not self.enabled or firm_from_account(account_id) is None:
            return
        try:
            await self.apply(account_id)
        except Exception as exc:
            logger.warning(f"Riesgo automático en {account_id}: {exc}")

    async def apply_missing(self) -> list[dict]:
        out = []
        for snap in self.accounts.all():
            if self.needs(snap.account_id):
                info = await self.apply(snap.account_id)
                if info:
                    out.append(info)
        return out
