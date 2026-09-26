from fastapi import APIRouter, Depends, HTTPException, Request

from tradepilot.api.auth import require_token
from tradepilot.api.schemas import AccountSettings, FlattenAllRequest, FlattenRequest, KillSwitchRequest, ScheduleRequest, LinkRequest, MasterRequest, MockEventRequest, RiskLimitUpsert, PeakRequest, CommissionsRequest, RuleCreate, RuleUpdate, EntryPreset, NotifyRequest, DiscoverChatRequest
from tradepilot.container import Container
from tradepilot.domain.risk import RiskLimit, Schedule

router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _c(request: Request) -> Container:
    return request.app.state.container


def _ver(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


@router.get("/health")
def health(request: Request):
    c = _c(request)
    v = c.accounts.health().addon_version
    outdated = c.settings.ENGINE_MODE == "ninja" and (v is None or _ver(v) < _ver(c.settings.MIN_ADDON_VERSION))
    web_build = None
    try:
        from datetime import datetime
        from pathlib import Path
        idx = Path(c.settings.WEB_DIST) / "index.html"
        if idx.exists():
            web_build = datetime.fromtimestamp(idx.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    except Exception:
        web_build = None
    return {
        "app": c.settings.APP_NAME,
        "mode": c.settings.ENGINE_MODE,
        "web_build": web_build,          # cuándo se compiló la consola que sirve el engine (para detectar cachés viejas)
        "addon_outdated": outdated,
        "min_addon_version": c.settings.MIN_ADDON_VERSION,
        "bridge": c.accounts.health(),
        "risk": c.risk.state(),
        "stats": c.replication.stats,
        "ws_clients": request.app.state.ws_hub.count,
    }


@router.get("/accounts")
def accounts(request: Request):
    return _c(request).accounts.all()


@router.post("/master")
async def set_master(body: MasterRequest, request: Request):
    """Cambia la cuenta maestra en NinjaTrader (addon v1.2+) y lo registra en la auditoría."""
    c = _c(request)
    try:
        applied = await c.bridge.set_master(body.account.strip())
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    c.audit.log("MASTER_CHANGED", f"Cuenta maestra cambiada a {applied}", source=applied)
    await c.bus.publish("broker.health", c.accounts.health().model_dump(mode="json"))
    return {"master_account": applied}


@router.patch("/accounts/{account_id}")
async def account_settings(account_id: str, body: AccountSettings, request: Request):
    """Activar/desactivar una cuenta o ponerle alias desde la consola."""
    return await _c(request).accounts.set_settings(account_id, body.enabled, body.alias, body.auto)


@router.put("/accounts/{account_id}/peak")
async def set_peak(account_id: str, body: PeakRequest, request: Request):
    """Fija a mano el máximo (marca de agua) del drawdown dinámico; peak=null lo reinicia al valor actual de la cuenta."""
    try:
        return await _c(request).accounts.set_peak(account_id, body.peak)
    except KeyError:
        raise HTTPException(404, f"cuenta {account_id} desconocida")


@router.delete("/accounts/{account_id}", status_code=204)
async def forget_account(account_id: str, request: Request):
    if not await _c(request).accounts.forget(account_id):
        raise HTTPException(409, "Solo se pueden olvidar cuentas que el bróker ya no reporta")


@router.put("/accounts/{follower}/link")
async def link_account(follower: str, body: LinkRequest, request: Request):
    """Vincular (o actualizar) una cuenta seguidora al maestro con un clic."""
    c = _c(request)
    try:
        opts = body.model_dump(exclude_unset=True, exclude={"master_account", "multiplier", "enabled"})
        rule = c.replication.link(body.master_account, follower, body.multiplier, body.enabled, **opts)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    await c.accounts.watch(follower)
    return rule


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
        opts = body.model_dump(exclude_unset=True, exclude={"master_account", "follower_account", "multiplier", "symbol_filter", "enabled"})
        return _c(request).replication.add_rule(body.master_account, body.follower_account, body.multiplier,
                                                body.symbol_filter, body.enabled, **opts)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: str, body: RuleUpdate, request: Request):
    rule = _c(request).replication.update_rule(rule_id, **body.model_dump(exclude_unset=True))
    if rule is None:
        raise HTTPException(404, "Regla no encontrada")
    return rule


@router.put("/rules/entry")
def set_entry_for_all(body: EntryPreset, request: Request):
    """Mismo modo de entrada para todas las seguidoras de la maestra actual (p. ej. "límite al precio del maestro ±0")."""
    c = _c(request)
    return c.replication.set_entry_for_all(c.bridge.health.master_account, **body.model_dump(exclude_unset=True))


@router.get("/execution")
def execution_quality(request: Request, limit: int = 40):
    """Últimas operaciones copiadas con el fill de cada seguidora: precio, deslizamiento en ticks y tiempos."""
    return _c(request).replication.recent_executions(min(max(1, limit), 300))


@router.get("/notifications")
def get_notifications(request: Request):
    c = _c(request)
    if c.notify is None:
        raise HTTPException(503, "avisos no disponibles")
    return c.notify.state()


@router.put("/notifications")
def set_notifications(body: NotifyRequest, request: Request):
    """Avisos por Telegram: token del bot, chat y qué eventos mandar."""
    from tradepilot.services.notify_service import NotifyConfig
    c = _c(request)
    if c.notify is None:
        raise HTTPException(503, "avisos no disponibles")
    cfg = c.notify.set_config(NotifyConfig(**body.model_dump()))
    c.audit.log("NOTIFY_SET", f"Avisos por Telegram {'activados' if cfg.enabled else 'desactivados'} ({len(cfg.events)} tipos de evento)")
    return c.notify.state()


@router.post("/notifications/test")
async def test_notifications(request: Request):
    c = _c(request)
    if c.notify is None:
        raise HTTPException(503, "avisos no disponibles")
    return await c.notify.test()


@router.post("/notifications/discover-chat")
async def discover_chat(body: DiscoverChatRequest, request: Request):
    c = _c(request)
    if c.notify is None:
        raise HTTPException(503, "avisos no disponibles")
    return await c.notify.discover_chat(body.bot_token)


@router.get("/news")
async def news(request: Request, days: int = 7, countries: str | None = None, min_impact: str = "low"):
    """Calendario económico (ForexFactory): eventos de los próximos `days` días; `countries` = USD,EUR…; `min_impact` = low|medium|high."""
    c = _c(request)
    if c.news is None:
        raise HTTPException(503, "calendario económico no disponible")
    return await c.news.get(days, [x for x in (countries or "").split(",") if x], min_impact)


@router.get("/stats/month")
def stats_month(request: Request, month: str | None = None, account: str | None = None):
    """Calendario: días (P&L del bróker o suma de operaciones, comisiones, operaciones, aciertos) y operaciones del mes.
    `month` = AAAA-MM (por defecto el actual); `account` vacío = todas las cuentas."""
    from datetime import datetime
    c = _c(request)
    if c.performance is None:
        raise HTTPException(503, "rendimiento no disponible")
    try:
        y, m = (int(x) for x in (month or datetime.now().strftime("%Y-%m")).split("-"))
        if not 1 <= m <= 12:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "month debe ser AAAA-MM")
    return c.performance.month(y, m, account or None)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: str, request: Request):
    if not _c(request).replication.delete_rule(rule_id):
        raise HTTPException(404, "Regla no encontrada")


@router.get("/pnl")
def pnl_history(request: Request, hours: float = 24):
    """Curva de P&L por cuenta: {cuenta: [[ts, pnl], ...]} desde hace `hours` horas (muestras cada 15 s)."""
    from datetime import datetime, timedelta
    c = _c(request)
    since = (datetime.now() - timedelta(hours=max(0.1, min(hours, 96)))).isoformat(timespec="seconds")
    return {acc: [[ts, pnl] for ts, pnl in rows] for acc, rows in c.store.get_pnl_samples(since).items()}


@router.get("/audit")
def audit(request: Request, limit: int = 100, event_type: str | None = None):
    return _c(request).audit.recent(min(limit, 500), event_type)


@router.get("/risk")
def risk(request: Request):
    return _c(request).risk.state()


@router.post("/risk/kill-switch")
async def kill_switch(body: KillSwitchRequest, request: Request):
    return await _c(request).risk.kill(body.active, body.reason, body.flatten, body.flatten_master)


@router.post("/risk/flatten-all")
async def flatten_all(body: FlattenAllRequest, request: Request):
    """Cierre de emergencia de todas las seguidoras vinculadas (y la maestra si se pide)."""
    results = await _c(request).risk.flatten_all(body.include_master, body.reason or "")
    return {"results": results}


@router.post("/accounts/{account_id}/flatten")
async def flatten_account(account_id: str, body: FlattenRequest, request: Request):
    try:
        return {"result": await _c(request).risk.flatten(account_id, body.reason or "")}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@router.post("/accounts/{account_id}/resync")
async def resync_account(account_id: str, request: Request):
    """Igualar la seguidora a la maestra x multiplicador mandando la diferencia a mercado."""
    c = _c(request)
    if c.sync.expected_positions(account_id) is None:
        raise HTTPException(400, "La cuenta no está vinculada a la maestra actual")
    return {"sent": await c.sync.resync(account_id), "diff": c.sync.diff(account_id)}


@router.put("/risk/schedule")
def set_schedule(body: ScheduleRequest, request: Request):
    return _c(request).risk.set_schedule(Schedule(**body.model_dump()))


@router.post("/risk/reopen")
def reopen_session(request: Request):
    _c(request).risk.reopen_session()
    return _c(request).risk.state()


@router.put("/risk/commissions")
def set_commissions(body: CommissionsRequest, request: Request):
    """Comisión por contrato y lado por símbolo: el objetivo y la pérdida diaria se miden en neto."""
    from tradepilot.domain.risk import Commissions
    if any(v < 0 for v in body.rates.values()):
        raise HTTPException(422, "las comisiones no pueden ser negativas")
    return _c(request).risk.set_commissions(Commissions(**body.model_dump()))


@router.put("/risk/limits")
def upsert_limit(body: RiskLimitUpsert, request: Request):
    try:
        return _c(request).risk.upsert_limit(RiskLimit(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.delete("/risk/limits/{account_id}", status_code=204)
def delete_limit(account_id: str, request: Request):
    if not _c(request).risk.delete_limit(account_id):
        raise HTTPException(404, f"{account_id} no tiene límites configurados")


@router.post("/mock/master-event")
async def mock_master_event(body: MockEventRequest, request: Request):
    """Solo en modo mock: dispara un evento del maestro a mano (útil para demos y pruebas)."""
    c = _c(request)
    bridge = c.bridge
    if not hasattr(bridge, "emit_master_event"):
        raise HTTPException(400, "Solo disponible en ENGINE_MODE=mock")
    overrides = {k: v for k, v in body.model_dump().items() if v is not None}
    return await bridge.emit_master_event(**overrides)
