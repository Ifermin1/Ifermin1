from fastapi import APIRouter, Depends, HTTPException, Request

from tradepilot.api.auth import require_token
from tradepilot.api.schemas import AccountSettings, KillSwitchRequest, LinkRequest, MockEventRequest, RiskLimitUpsert, RuleCreate, RuleUpdate
from tradepilot.container import Container
from tradepilot.domain.risk import RiskLimit

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _c(request: Request) -> Container:
    return request.app.state.container


@router.get("/health")
def health(request: Request):
    c = _c(request)
    return {
        "app": c.settings.APP_NAME,
        "mode": c.settings.ENGINE_MODE,
        "bridge": c.accounts.health(),
        "risk": c.risk.state(),
        "stats": c.replication.stats,
        "ws_clients": request.app.state.ws_hub.count,
    }


@router.get("/accounts")
def accounts(request: Request):
    return _c(request).accounts.all()


@router.patch("/accounts/{account_id}")
async def account_settings(account_id: str, body: AccountSettings, request: Request):
    """Activar/desactivar una cuenta o ponerle alias desde la consola."""
    return await _c(request).accounts.set_settings(account_id, body.enabled, body.alias, body.auto)


@router.delete("/accounts/{account_id}", status_code=204)
async def forget_account(account_id: str, request: Request):
    if not await _c(request).accounts.forget(account_id):
        raise HTTPException(409, "Solo se pueden olvidar cuentas que el bróker ya no reporta")


@router.put("/accounts/{follower}/link")
def link_account(follower: str, body: LinkRequest, request: Request):
    """Vincular (o actualizar) una cuenta seguidora al maestro con un clic."""
    try:
        return _c(request).replication.link(body.master_account, follower, body.multiplier, body.enabled)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.delete("/accounts/{follower}/link", status_code=204)
def unlink_account(follower: str, master_account: str, request: Request):
    if not _c(request).replication.unlink(master_account, follower):
        raise HTTPException(404, "No hay vínculo entre esas cuentas")


@router.get("/rules")
def list_rules(request: Request):
    return _c(request).replication.rules


@router.post("/rules", status_code=201)
def create_rule(body: RuleCreate, request: Request):
    try:
        return _c(request).replication.add_rule(body.master_account, body.follower_account, body.multiplier,
                                                body.symbol_filter, body.enabled)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: str, body: RuleUpdate, request: Request):
    rule = _c(request).replication.update_rule(rule_id, **body.model_dump(exclude_unset=True))
    if rule is None:
        raise HTTPException(404, "Regla no encontrada")
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: str, request: Request):
    if not _c(request).replication.delete_rule(rule_id):
        raise HTTPException(404, "Regla no encontrada")


@router.get("/audit")
def audit(request: Request, limit: int = 100, event_type: str | None = None):
    return _c(request).audit.recent(min(limit, 500), event_type)


@router.get("/risk")
def risk(request: Request):
    return _c(request).risk.state()


@router.post("/risk/kill-switch")
def kill_switch(body: KillSwitchRequest, request: Request):
    return _c(request).risk.set_kill_switch(body.active, body.reason)


@router.put("/risk/limits")
def upsert_limit(body: RiskLimitUpsert, request: Request):
    return _c(request).risk.upsert_limit(RiskLimit(**body.model_dump()))


@router.post("/mock/master-event")
async def mock_master_event(body: MockEventRequest, request: Request):
    """Solo en modo mock: dispara un evento del maestro a mano (útil para demos y pruebas)."""
    c = _c(request)
    bridge = c.bridge
    if not hasattr(bridge, "emit_master_event"):
        raise HTTPException(400, "Solo disponible en ENGINE_MODE=mock")
    overrides = {k: v for k, v in body.model_dump().items() if v is not None}
    return await bridge.emit_master_event(**overrides)
