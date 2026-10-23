from fastapi import APIRouter, Depends, HTTPException, Request

from tradepilot.api.auth import require_token
from tradepilot.api.schemas import AccountSettings, FlattenAllRequest, FlattenRequest, KillSwitchRequest, ScheduleRequest, LinkRequest, MasterRequest, MockEventRequest, RiskLimitUpsert, PeakRequest, CommissionsRequest, RuleCreate, RuleUpdate, EntryPreset, NotifyRequest, DiscoverChatRequest, CashEventCreate, CashEventUpdate, AnalysisParams, ManualOrderRequest, CancelOrderRequest, ModifyOrderRequest, ClosePositionRequest, ProtectRequest, TrailRequest
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


def weak_token(token: str) -> bool:
    return len(token) < 16 or token.lower() in ("cambiame", "changeme", "password", "token")


def public_url(c: Container) -> str | None:
    """URL pública de la consola: PUBLIC_URL del .env o el fichero que escribe scripts/publish.ps1 (túnel)."""
    from pathlib import Path
    if c.settings.PUBLIC_URL.strip():
        return c.settings.PUBLIC_URL.strip().rstrip("/")
    try:
        p = Path(c.settings.PUBLIC_URL_FILE)
        if p.exists():
            txt = p.read_text(encoding="utf-8").strip().splitlines()
            return txt[0].strip().rstrip("/") if txt and txt[0].strip() else None
    except Exception:
        return None
    return None


@router.get("/public-url/qr.svg", include_in_schema=False)
def public_url_qr(request: Request, url: str | None = None):
    """QR (SVG) de la URL pública para escanearla con el teléfono; `url` permite pedir el de otra dirección."""
    import qrcode
    import qrcode.image.svg
    from fastapi.responses import Response
    target = (url or public_url(_c(request)) or "").strip()
    if not target.startswith(("http://", "https://")):
        raise HTTPException(404, "no hay URL pública configurada")
    img = qrcode.make(target, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    return Response(content=img.to_string(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


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
        "public_url": public_url(c),     # URL del túnel (scripts/publish.ps1) o PUBLIC_URL; None si no está publicada
        "token_weak": weak_token(c.settings.API_TOKEN),
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
    return await _c(request).accounts.set_settings(account_id, body.enabled, body.alias, body.auto, body.firm, body.plan, body.plan_size)


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


ANALYSIS_KEY = "ui.analysis"


@router.get("/ui/analysis")
def get_analysis_params(request: Request):
    """Parámetros de la pestaña Análisis (o los de fábrica si nunca se guardaron)."""
    import json
    raw = _c(request).store.get_kv(ANALYSIS_KEY)
    if not raw:
        return AnalysisParams().model_dump()
    try:
        return AnalysisParams.model_validate(json.loads(raw)).model_dump()
    except Exception:
        return AnalysisParams().model_dump()


@router.put("/ui/analysis")
def put_analysis_params(body: AnalysisParams, request: Request):
    _c(request).store.set_kv(ANALYSIS_KEY, body.model_dump_json())
    return body.model_dump()


@router.get("/stats/analysis")
def stats_analysis(request: Request, day_from: str | None = None, day_to: str | None = None, account: str | None = None):
    """Análisis de un periodo: operaciones, filas por día (bruto, comisiones, P&L del bróker, saldo de cierre, retiros) y
    movimientos de caja. `day_from`/`day_to` = AAAA-MM-DD (por defecto los últimos 90 días); `account` vacío = todas."""
    from datetime import date, timedelta
    c = _c(request)
    if c.performance is None:
        raise HTTPException(503, "rendimiento no disponible")
    today = date.today()
    d0 = day_from or (today - timedelta(days=90)).isoformat()
    d1 = day_to or (today + timedelta(days=1)).isoformat()
    try:
        date.fromisoformat(d0); date.fromisoformat(d1)
    except ValueError:
        raise HTTPException(400, "fechas inválidas (AAAA-MM-DD)")
    if d1 < d0:
        raise HTTPException(400, "el fin del periodo es anterior al inicio")
    return c.performance.analysis(d0, d1, account or None)


@router.get("/cash")
def list_cash(request: Request, account: str | None = None):
    """Movimientos de caja (retiros solicitados, pagos recibidos, depósitos) anotados a mano; `account` vacío = todas."""
    return _c(request).store.list_cash_events(account or None)


@router.post("/cash", status_code=201)
def add_cash(body: CashEventCreate, request: Request):
    from datetime import datetime
    c = _c(request)
    e = body.model_dump()
    e["created_at"] = datetime.now().isoformat(timespec="seconds")
    e["id"] = c.store.add_cash_event(e)
    c.audit.log("CASH_EVENT", f"{body.kind} de {body.amount:.2f} USD ({body.status})", body.account_id, None, {"id": e["id"], **body.model_dump()})
    return e


@router.patch("/cash/{event_id}")
def update_cash(event_id: int, body: CashEventUpdate, request: Request):
    c = _c(request)
    if c.store.get_cash_event(event_id) is None:
        raise HTTPException(404, "movimiento no encontrado")
    fields = {k: v for k, v in body.model_dump().items() if v is not None or k == "paid"}
    if "paid" in fields and fields["paid"] is None and body.model_fields_set.isdisjoint({"paid"}):
        del fields["paid"]
    c.store.update_cash_event(event_id, fields)
    return c.store.get_cash_event(event_id)


@router.delete("/cash/{event_id}", status_code=204)
def delete_cash(event_id: int, request: Request):
    c = _c(request)
    if c.store.get_cash_event(event_id) is None:
        raise HTTPException(404, "movimiento no encontrado")
    c.store.delete_cash_event(event_id)
    return None


# ---- operativa manual (pestaña Operar) ----
def _trade(c: Container):
    if c.trade is None:
        raise HTTPException(503, "operativa manual no disponible")
    return c.trade


@router.post("/trade/order")
async def trade_order(body: ManualOrderRequest, request: Request):
    """Orden manual en cualquier cuenta (en la maestra se replica a las seguidoras). Idempotente por `client_id`: reenviar la
    misma petición devuelve el mismo resultado. 422 si no pasa las validaciones; 502 si NinjaTrader la rechaza o no confirma."""
    c = _c(request)
    try:
        return await _trade(c).place(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


@router.post("/trade/cancel")
async def trade_cancel(body: CancelOrderRequest, request: Request):
    c = _c(request)
    try:
        return await _trade(c).cancel(body.account, body.order_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


@router.post("/trade/modify")
async def trade_modify(body: ModifyOrderRequest, request: Request):
    c = _c(request)
    try:
        return await _trade(c).modify(body.account, body.order_id, body.quantity, body.limit_price, body.stop_price)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


@router.post("/trade/close")
async def trade_close(body: ClosePositionRequest, request: Request):
    """Cierra solo la posición de ese instrumento con una orden a mercado contraria."""
    c = _c(request)
    try:
        return await _trade(c).close_position(body.account, body.symbol, body.client_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


@router.post("/trade/protect")
async def trade_protect(body: ProtectRequest, request: Request):
    """Stop a breakeven, o stop/objetivo a un precio: modifica las salidas vivas de la posición o las crea (en OCO si son nuevas las dos)."""
    c = _c(request)
    try:
        return await _trade(c).protect(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


@router.get("/trade/protect")
def trade_protect_preview(account: str, symbol: str, request: Request):
    """Posición y sus salidas vivas (stops y objetivos) para el panel Proteger."""
    c = _c(request)
    try:
        return _trade(c).protect_preview(account, symbol)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/trade/trail")
async def trade_trail(body: TrailRequest, request: Request):
    """Activa (o reconfigura) el trailing stop de una posición: fixed (distancia fija), adaptive (k × ATR y volumen) o ai (Claude)."""
    c = _c(request)
    if c.trail is None:
        raise HTTPException(503, "trailing no disponible")
    try:
        return await c.trail.start_trail(body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


@router.delete("/trade/trail")
async def trade_trail_stop(account: str, symbol: str, request: Request):
    c = _c(request)
    if c.trail is None:
        raise HTTPException(503, "trailing no disponible")
    t = await c.trail.stop_trail(account, symbol)
    if t is None:
        raise HTTPException(404, "esa posición no tiene trailing")
    return t


@router.get("/trade/trails")
def trade_trails(request: Request):
    """Trailings activos con su estado (stop actual, mejor precio, distancia en vigor, razón de la IA) y el mercado que ven."""
    c = _c(request)
    if c.trail is None:
        raise HTTPException(503, "trailing no disponible")
    trails = c.trail.list()
    return {"trails": trails, "stats": c.trail.stats, "ai_interval_s": c.trail.ai_interval_s, "ai": c.news.ai.status() if c.news else None,
            "market": {t["symbol"]: c.market.snapshot(t["symbol"]) for t in trails} if c.market else {}}


@router.get("/trade/status")
def trade_status(request: Request):
    """Para la pestaña Operar: si se puede operar (addon, conexión, kill switch), límites y latencias medidas."""
    c = _c(request)
    t = _trade(c)
    h = c.accounts.health()
    reason = None
    try:
        t._check_addon()
    except ValueError as exc:
        reason = str(exc)
    if reason is None and c.risk.kill_switch:
        reason = "kill switch activo: solo cerrar"
    from datetime import datetime, timedelta
    hb = (datetime.now() - h.last_heartbeat).total_seconds() if h.last_heartbeat else None
    symbols: list[str] = []
    for a in c.accounts.all():
        symbols += [p.symbol for p in a.open_positions] + [o.symbol for o in a.working_orders]
    try:
        symbols += c.store.recent_symbols((datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d"))
    except Exception:
        pass
    if c.settings.ENGINE_MODE != "ninja":
        from tradepilot.infrastructure.brokers.mock import SYMBOLS
        symbols += SYMBOLS
    seen: set[str] = set()
    symbols = [x for x in symbols if x and not (x in seen or seen.add(x))]
    return {"ready": reason is None, "reason": reason, "max_qty": c.settings.MANUAL_MAX_QTY, "mode": c.settings.ENGINE_MODE, "symbols": symbols,
            "addon_version": h.addon_version, "required_addon": c.settings.MANUAL_ADDON_VERSION, "ping_ms": h.ping_ms, "last_order_ms": h.last_order_ms,
            "heartbeat_age_s": round(hb, 1) if hb is not None else None, "stats": t.stats, "master": h.master_account,
            "trails": len(c.trail.trails) if c.trail else 0, "ai_enabled": bool(c.news and c.news.ai.enabled)}


@router.get("/news")
async def news(request: Request, days: int = 7, countries: str | None = None, min_impact: str = "low"):
    """Calendario económico (ForexFactory): eventos de los próximos `days` días; `countries` = USD,EUR…; `min_impact` = low|medium|high."""
    c = _c(request)
    if c.news is None:
        raise HTTPException(503, "calendario económico no disponible")
    return await c.news.get(days, [x for x in (countries or "").split(",") if x], min_impact)


@router.get("/news/alerts")
async def news_alerts(request: Request):
    """Para Inicio: noticias vigiladas a punto de salir (con análisis previo), la siguiente y los últimos datos publicados."""
    c = _c(request)
    if c.news is None:
        raise HTTPException(503, "calendario económico no disponible")
    await c.news.refresh()
    return c.news.alerts()


@router.get("/news/stats")
async def news_stats(request: Request, days: int = 90):
    """Métricas: operaciones dentro de ±15 min de una noticia frente al resto, reacción media del precio y sorpresas."""
    c = _c(request)
    if c.news is None:
        raise HTTPException(503, "calendario económico no disponible")
    return c.news.stats(days)


@router.post("/news/analyze/{event_id}")
async def news_analyze(event_id: str, request: Request, kind: str = "pre", force: bool = False):
    """Genera (o regenera con `force`) el análisis de un evento: `kind` = pre (impacto posible) | post (resultado)."""
    c = _c(request)
    if c.news is None:
        raise HTTPException(503, "calendario económico no disponible")
    if kind not in ("pre", "post"):
        raise HTTPException(422, "kind debe ser pre o post")
    e = c.news.find(event_id)
    if e is None:
        raise HTTPException(404, "evento no encontrado")
    if kind == "post" and not e.get("actual"):
        raise HTTPException(409, "el dato aún no se ha publicado")
    return {"event": e, "analysis": await c.news.ensure_analysis(e, kind, force=force), "ai": c.news.ai.status()}


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
