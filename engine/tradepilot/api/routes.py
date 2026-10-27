from fastapi import APIRouter, Depends, HTTPException, Request

from tradepilot.api.auth import require_token
from tradepilot.api.schemas import AccountSettings, FlattenAllRequest, FlattenRequest, KillSwitchRequest, ScheduleRequest, LinkRequest, MasterRequest, MockEventRequest, RiskLimitUpsert, PeakRequest, CommissionsRequest, RuleCreate, RuleUpdate, EntryPreset, NotifyRequest, DiscoverChatRequest, CashEventCreate, CashEventUpdate, AnalysisParams, ManualOrderRequest, CancelOrderRequest, ModifyOrderRequest, ClosePositionRequest, ProtectRequest, TrailRequest, DayLossRequest, AutoRiskRequest, RiskPresets, ApplyPresetRequest, TagsRequest, OcoEntryRequest, TradeTemplates, ReviewRequest, CheckEntryRequest, AskRequest, PushSubscribeRequest, PushUnsubscribeRequest, ConfigImportRequest, AssistantRequest, PlanRequest, DigestConfigRequest, DigestRunRequest, TiltConfigRequest, TiltResumeRequest
from tradepilot.container import Container
from tradepilot.domain.risk import RiskLimit, Schedule

import re as _re
_DAY_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
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
        "role": getattr(request.state, "role", "full"),      # "read" = token de solo lectura: la consola se pone en modo lectura
        "read_token_set": bool(c.settings.API_READ_TOKEN),
        "addon_outdated": outdated,
        "min_addon_version": c.settings.MIN_ADDON_VERSION,
        "bridge": c.accounts.health(),
        "risk": c.risk.state(),
        "watchdog": c.watchdog.state() if c.watchdog is not None else None,
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
    return await _c(request).accounts.set_settings(account_id, body.enabled, body.alias, body.auto, body.firm, body.plan, body.plan_size, body.tags)


@router.put("/accounts/tags")
async def accounts_tags(body: TagsRequest, request: Request):
    """Añade o quita etiquetas (grupos) a varias cuentas de una vez."""
    c = _c(request)
    out = []
    for acc in body.accounts:
        snap = c.accounts.find(acc)
        if snap is None:
            continue
        tags = [t for t in snap.tags if t.lower() not in {x.lower() for x in body.remove}] + [t for t in body.add if t.lower() not in {x.lower() for x in snap.tags}]
        out.append(await c.accounts.set_settings(snap.account_id, tags=tags))
    return out


PRESETS_KEY = "ui.risk_presets"
DEFAULT_PRESETS = [
    {"id": "conservador", "name": "Conservador", "multiplier": 1.0, "max_daily_loss": 500, "max_daily_profit": 800, "max_position_size": 2, "drawdown_buffer": 250, "entry_mode": "limit"},
    {"id": "moderado", "name": "Moderado", "multiplier": 1.0, "max_daily_loss": 1000, "max_daily_profit": 1500, "max_position_size": 5, "drawdown_buffer": 150, "entry_mode": "market"},
    {"id": "agresivo", "name": "Agresivo", "multiplier": 2.0, "max_daily_loss": 0, "max_daily_profit": 0, "max_position_size": 10, "drawdown_buffer": 50, "entry_mode": "market"},
]


def _presets(c: Container) -> list[dict]:
    import json as _json
    raw = c.store.get_kv(PRESETS_KEY)
    if raw:
        try:
            return _json.loads(raw)
        except ValueError:
            pass
    return [dict(p) for p in DEFAULT_PRESETS]


@router.get("/ui/risk-presets")
def get_presets(request: Request):
    return {"presets": _presets(_c(request)), "defaults": DEFAULT_PRESETS}


@router.put("/ui/risk-presets")
def put_presets(body: RiskPresets, request: Request):
    import json as _json
    ids = [p.id for p in body.presets]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "ids de preset repetidos")
    c = _c(request)
    c.store.set_kv(PRESETS_KEY, _json.dumps([p.model_dump() for p in body.presets]))
    return {"presets": _presets(c), "defaults": DEFAULT_PRESETS}


@router.post("/risk/preset")
async def apply_preset(body: ApplyPresetRequest, request: Request):
    """Aplica un preset a varias cuentas: límites (pérdida/corte diario, contratos, colchón) y, si copian, multiplicador y modo de entrada."""
    c = _c(request)
    preset = next((p for p in _presets(c) if p["id"] == body.preset_id), None)
    if preset is None:
        raise HTTPException(404, "preset no encontrado")
    master = c.accounts.health().master_account or ""
    applied = []
    for acc in body.accounts:
        snap = c.accounts.find(acc)
        if snap is None:
            continue
        prev = c.risk.limits.get(snap.account_id)
        limit = (prev.model_copy() if prev else RiskLimit(account_id=snap.account_id)).model_copy(update={
            "max_daily_loss": float(preset["max_daily_loss"]), "max_daily_profit": float(preset["max_daily_profit"]),
            "max_position_size": int(preset["max_position_size"]), "drawdown_buffer": float(preset["drawdown_buffer"])})
        try:
            c.risk.upsert_limit(limit)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        rule = next((r for r in c.replication.rules if r.follower_account.lower() == snap.account_id.lower() and not r.symbol_filter and (not master or r.master_matches(master))), None)
        if rule is not None:
            c.replication.update_rule(rule.id, multiplier=float(preset["multiplier"]), entry_mode=preset["entry_mode"])
        c.audit.log("PRESET_APPLIED", f"{snap.account_id}: preset {preset['name']} (pérdida diaria {preset['max_daily_loss']}, corte {preset['max_daily_profit']}, "
                    f"máx {preset['max_position_size']} contratos, colchón {preset['drawdown_buffer']}" + (f", ×{preset['multiplier']} {preset['entry_mode']}" if rule else "") + ")",
                    target=snap.account_id)
        applied.append(snap.account_id)
    await c.accounts.publish_accounts(force=True)
    return {"applied": applied, "preset": preset}


@router.get("/advice")
def advice(request: Request):
    """Recomendaciones por cuenta (desincronización, drawdown, racha, sin límites…) con acciones de un clic."""
    c = _c(request)
    if c.advisor is None:
        raise HTTPException(503, "recomendaciones no disponibles")
    return c.advisor.advise()


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


@router.get("/push")
def push_state(request: Request):
    """Web Push: clave pública VAPID (para suscribir el navegador) y dispositivos suscritos."""
    c = _c(request)
    if c.push is None:
        raise HTTPException(503, "push no disponible")
    return c.push.state()


@router.post("/push/subscribe")
def push_subscribe(body: PushSubscribeRequest, request: Request):
    c = _c(request)
    if c.push is None:
        raise HTTPException(503, "push no disponible")
    try:
        st = c.push.subscribe(body.subscription, body.ua)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    c.audit.log("PUSH_SUBSCRIBED", f"Avisos push activados en un dispositivo ({st['subscriptions']} suscritos)")
    return st


@router.post("/push/unsubscribe")
def push_unsubscribe(body: PushUnsubscribeRequest, request: Request):
    c = _c(request)
    if c.push is None:
        raise HTTPException(503, "push no disponible")
    return c.push.unsubscribe(body.endpoint)


@router.post("/push/test")
async def push_test(request: Request):
    c = _c(request)
    if c.push is None:
        raise HTTPException(503, "push no disponible")
    return await c.push.test()


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


@router.get("/tilt")
def tilt_state(request: Request):
    """Detector de tilt: configuración, señales por cuenta y pausas activas."""
    c = _c(request)
    if c.tilt is None:
        raise HTTPException(503, "tilt no disponible")
    return c.tilt.state()


@router.put("/tilt")
def tilt_config(body: TiltConfigRequest, request: Request):
    c = _c(request)
    if c.tilt is None:
        raise HTTPException(503, "tilt no disponible")
    c.tilt.set_config(**body.model_dump(exclude_unset=True))
    c.audit.log("TILT_SET", f"Detector de tilt: {'activo' if c.tilt.config['enabled'] else 'desactivado'} · racha {c.tilt.config['streak']} · pausa automática {c.tilt.config['auto_pause_min']} min")
    return c.tilt.state()


@router.post("/tilt/resume")
def tilt_resume(body: TiltResumeRequest, request: Request):
    c = _c(request)
    if c.tilt is None:
        raise HTTPException(503, "tilt no disponible")
    return {"resumed": c.tilt.resume(body.account, "a mano desde la consola"), **c.tilt.state()}


@router.get("/digest")
def digest_state(request: Request):
    """Briefing de la mañana y cierre de sesión: configuración (horas) y los últimos textos."""
    c = _c(request)
    if c.digest is None:
        raise HTTPException(503, "resúmenes no disponibles")
    return c.digest.state()


@router.put("/digest")
def digest_config(body: DigestConfigRequest, request: Request):
    c = _c(request)
    if c.digest is None:
        raise HTTPException(503, "resúmenes no disponibles")
    c.digest.set_config(**body.model_dump(exclude_unset=True))
    c.audit.log("DIGEST_SET", f"Resúmenes automáticos: {'activados' if c.digest.config['enabled'] else 'desactivados'} · briefing {c.digest.config['brief_time']} · cierre {c.digest.config['review_time']}")
    return c.digest.state()


@router.post("/digest/run")
async def digest_run(body: DigestRunRequest, request: Request):
    c = _c(request)
    if c.digest is None:
        raise HTTPException(503, "resúmenes no disponibles")
    return await (c.digest.morning_brief("manual") if body.kind == "brief" else c.digest.session_review("manual"))


@router.post("/assistant")
async def assistant_ask(body: AssistantRequest, request: Request):
    """Asistente por chat: responde consultas al momento y, si pide hacer algo, devuelve un plan para confirmar."""
    c = _c(request)
    if c.assistant is None:
        raise HTTPException(503, "asistente no disponible")
    try:
        return await c.assistant.handle(body.text, "consola")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/assistant/confirm")
async def assistant_confirm(body: PlanRequest, request: Request):
    c = _c(request)
    if c.assistant is None:
        raise HTTPException(503, "asistente no disponible")
    try:
        return await c.assistant.confirm(body.id)
    except ValueError as exc:
        raise HTTPException(410, str(exc))


@router.post("/assistant/cancel")
def assistant_cancel(body: PlanRequest, request: Request):
    c = _c(request)
    if c.assistant is None:
        raise HTTPException(503, "asistente no disponible")
    return {"cancelled": c.assistant.cancel(body.id)}


@router.get("/copilot")
def copilot_state(request: Request):
    """Copiloto en vivo: últimas propuestas sobre las posiciones abiertas (IA con clave; reglas sin ella)."""
    c = _c(request)
    if c.copilot is None:
        raise HTTPException(503, "copiloto no disponible")
    return c.copilot.state()


@router.post("/copilot/run")
async def copilot_run(request: Request):
    c = _c(request)
    if c.copilot is None:
        raise HTTPException(503, "copiloto no disponible")
    return await c.copilot.run("manual")


@router.post("/copilot/enabled")
def copilot_enabled(body: dict, request: Request):
    c = _c(request)
    if c.copilot is None:
        raise HTTPException(503, "copiloto no disponible")
    c.copilot.set_enabled(bool(body.get("enabled", True)))
    c.audit.log("COPILOT_SET", f"Copiloto en vivo {'activado' if c.copilot.enabled else 'desactivado'}")
    return c.copilot.state()


@router.get("/metrics/summary")
def metrics_summary(request: Request, hours: int = 168):
    """Salud: latencias y contadores por hora (hasta 30 días), por día, hora en curso, totales desde el arranque y estado actual."""
    c = _c(request)
    if c.metrics is None:
        raise HTTPException(503, "métricas no disponibles")
    return c.metrics.summary(min(max(1, hours), 24 * 30))


@router.get("/metrics")
def metrics_prometheus(request: Request):
    """Formato Prometheus (scrape con `authorization: credentials: <API_TOKEN>` o `?token=`)."""
    from fastapi.responses import PlainTextResponse
    c = _c(request)
    if c.metrics is None:
        raise HTTPException(503, "métricas no disponibles")
    return PlainTextResponse(c.metrics.prometheus(), media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/doctor")
def doctor_checks(request: Request):
    """Revisión de la instalación (lo mismo que `python -m tradepilot.doctor`): nivel ok/warn/bad/info por comprobación."""
    from tradepilot import doctor
    c = _c(request)
    checks = doctor.run_checks(c.settings, store=c.store, accounts=c.accounts, replication=c.replication, risk=c.risk, in_engine=True)
    return {"checks": checks, "counts": doctor.summary(checks)}


@router.get("/diagnostics.zip")
def diagnostics_zip(request: Request):
    """Zip de diagnóstico sin secretos (solo con el token completo)."""
    from fastapi.responses import Response
    from tradepilot.services.diagnostics import build_zip
    c = _c(request)
    if getattr(request.state, "role", "full") != "full":
        raise HTTPException(403, "Token de solo lectura: esta descarga no está permitida")
    from datetime import datetime
    data = build_zip(c)
    c.audit.log("DIAGNOSTICS", f"Zip de diagnóstico generado ({len(data) // 1024} KB)")
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename=tradepilotx-diagnostico-{datetime.now():%Y%m%d-%H%M}.zip"})


@router.get("/backup")
def backup_state(request: Request):
    """Copias de seguridad de la base de datos: lista, última, integridad."""
    c = _c(request)
    if c.backup is None:
        raise HTTPException(503, "copias no disponibles")
    return c.backup.state()


@router.post("/backup/run")
def backup_run(request: Request):
    c = _c(request)
    if c.backup is None:
        raise HTTPException(503, "copias no disponibles")
    try:
        return c.backup.backup_now("manual")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@router.get("/backup/download")
def backup_download(name: str, request: Request):
    """Descarga una copia (solo con el token completo: la base contiene el token del bot de Telegram y las suscripciones push)."""
    from fastapi.responses import FileResponse
    c = _c(request)
    if getattr(request.state, "role", "full") != "full":
        raise HTTPException(403, "Token de solo lectura: esta descarga no está permitida")
    p = c.backup.path_of(name) if c.backup is not None else None
    if p is None:
        raise HTTPException(404, "copia no encontrada")
    return FileResponse(str(p), media_type="application/octet-stream", filename=name)


@router.get("/config/export")
def config_export(request: Request):
    """Configuración sin secretos (reglas, límites, presets, horario, comisiones, cuentas, plantillas, avisos) para llevarla a otro PC."""
    from fastapi.responses import JSONResponse
    c = _c(request)
    if c.backup is None:
        raise HTTPException(503, "no disponible")
    data = c.backup.export_config()
    return JSONResponse(data, headers={"Content-Disposition": f"attachment; filename=tradepilotx-config-{data['exported_at'][:10]}.json"})


@router.post("/config/import")
async def config_import(body: ConfigImportRequest, request: Request):
    c = _c(request)
    if c.backup is None:
        raise HTTPException(503, "no disponible")
    try:
        return await c.backup.import_config(body.data, body.replace_rules)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/reconcile")
async def reconcile_last(request: Request):
    """Último informe de reconciliación (al arrancar o a petición): órdenes a medias, posiciones sin stop, entradas
    pendientes, seguidoras desincronizadas, trailings sin posición."""
    c = _c(request)
    if c.reconcile is None:
        raise HTTPException(503, "reconciliación no disponible")
    return c.reconcile.last or {"ran_at": None, "reason": None, "findings": [], "counts": {}, "accounts": 0}


@router.post("/reconcile/run")
async def reconcile_run(request: Request):
    c = _c(request)
    if c.reconcile is None:
        raise HTTPException(503, "reconciliación no disponible")
    await c.accounts.sync_once()
    return await c.reconcile.run("manual")


@router.get("/trade/orders")
def trade_orders(request: Request, limit: int = 50, today: bool = True):
    """Órdenes manuales guardadas (sobreviven a un reinicio): estado, respuesta y petición original."""
    from datetime import date
    return _trade(_c(request)).recent_orders(min(max(1, limit), 500), date.today().isoformat() if today else None)


@router.get("/hub")
def hub_state(request: Request):
    """Estado del reporter del hub: configurado o no, cola pendiente, último envío y errores."""
    c = _c(request)
    if c.hub is None:
        raise HTTPException(503, "hub no disponible")
    return c.hub.state()


@router.post("/hub/flush")
async def hub_flush(request: Request):
    """Fuerza un ciclo: recoge lo nuevo y lo envía al hub ahora."""
    c = _c(request)
    if c.hub is None:
        raise HTTPException(503, "hub no disponible")
    if not c.hub.enabled:
        raise HTTPException(400, "hub no configurado: pon HUB_URL y HUB_TOKEN en engine/.env y reinicia")
    r = await c.hub.tick()
    return {**r, **c.hub.state()}


@router.get("/report/weekly")
def report_weekly(request: Request, day: str | None = None, account: str | None = None, tag: str | None = None, format: str = "html"):
    """Informe semanal (lunes-domingo de `day`, hoy por defecto) por cuenta o por grupo (`tag`), como HTML imprimible o JSON."""
    from fastapi.responses import HTMLResponse
    c = _c(request)
    if c.report is None:
        raise HTTPException(503, "informes no disponibles")
    if day and not _DAY_RE.match(day):
        raise HTTPException(400, "day debe ser AAAA-MM-DD")
    data = c.report.weekly(day, account or None, tag or None)
    if format == "json":
        return data
    return HTMLResponse(c.report.weekly_html(data), headers={"Content-Disposition": f"inline; filename=informe-{data['from']}.html"})


@router.get("/trades.csv")
def trades_csv(request: Request, day_from: str, day_to: str, account: str | None = None, tag: str | None = None):
    """Operaciones del periodo en CSV (separador ;, coma decimal, UTF-8 con BOM: abre directo en Excel en español)."""
    from fastapi.responses import Response
    c = _c(request)
    if c.report is None:
        raise HTTPException(503, "informes no disponibles")
    if not (_DAY_RE.match(day_from) and _DAY_RE.match(day_to)):
        raise HTTPException(400, "day_from/day_to deben ser AAAA-MM-DD")
    body = c.report.trades_csv(day_from, day_to, account or None, tag or None)
    return Response(body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=operaciones-{day_from}-{day_to}.csv"})


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
        return await _trade(c).close_position(body.account, body.symbol, body.client_id, body.quantity)
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


@router.post("/trade/oco")
async def trade_oco(body: OcoEntryRequest, request: Request):
    """OCO de entrada: dos órdenes enlazadas (p. ej. compra stop arriba y venta stop abajo); la que ejecuta cancela la otra."""
    c = _c(request)
    try:
        req = body.model_dump()
        trail = req.pop("trail", None)
        out = await _trade(c).place_oco(req)
        if trail:
            for leg in out["legs"]:
                _trade(c)._pending_trails[(leg["account"].lower(), leg["symbol"].lower())] = {**trail, "account": leg["account"], "symbol": leg["symbol"], "expires": __import__("time").monotonic() + 6 * 3600}
            out["trail_pending"] = True
        return out
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))


TEMPLATES_KEY = "ui.trade_templates"


@router.get("/ui/trade-templates")
def get_templates(request: Request):
    import json as _json
    raw = _c(request).store.get_kv(TEMPLATES_KEY)
    try:
        return {"templates": _json.loads(raw) if raw else []}
    except ValueError:
        return {"templates": []}


@router.put("/ui/trade-templates")
def put_templates(body: TradeTemplates, request: Request):
    import json as _json
    ids = [t.id for t in body.templates]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "ids de plantilla repetidos")
    _c(request).store.set_kv(TEMPLATES_KEY, _json.dumps([t.model_dump() for t in body.templates]))
    return {"templates": [t.model_dump() for t in body.templates]}


@router.get("/market/bars")
def market_bars(symbol: str, request: Request, n: int = 120):
    """Velas de 1 minuto construidas con los ticks del addon (para el gráfico de Operar)."""
    c = _c(request)
    if c.market is None:
        raise HTTPException(503, "sin mercado")
    snap = c.market.snapshot(symbol)
    snap["n_bars"] = snap.pop("bars", 0)
    return {**snap, "symbol": symbol, "bars": [b.as_dict() | {"m": b.minute} for b in c.market.recent_bars(symbol, max(10, min(n, 240)))]}


# ---- coach IA ----
def _coach(c: Container):
    if c.coach is None:
        raise HTTPException(503, "coach no disponible")
    return c.coach


@router.post("/ai/review")
async def ai_review(body: ReviewRequest, request: Request):
    """Revisión de la sesión: patrones que costaron dinero y reglas para mañana (IA, o por reglas sin clave). Se guarda por día."""
    from datetime import date
    if body.day:
        try:
            date.fromisoformat(body.day)
        except ValueError:
            raise HTTPException(400, "día inválido (AAAA-MM-DD)")
    return await _coach(_c(request)).review(body.day or None, body.force)


@router.post("/ai/check-entry")
async def ai_check_entry(body: CheckEntryRequest, request: Request):
    """Semáforo antes de entrar: verde / ámbar / rojo con los motivos (riesgo, racha, noticias, volatilidad, tamaño)."""
    d = body.model_dump()
    use_ai = d.pop("ai")
    return await _coach(_c(request)).check_entry(d, use_ai)


@router.post("/ai/ask")
async def ai_ask(body: AskRequest, request: Request):
    """Chat con tus datos: pregunta en lenguaje natural sobre tu historial (requiere ANTHROPIC_API_KEY)."""
    try:
        return await _coach(_c(request)).ask(body.question, body.days)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


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


@router.put("/risk/day-loss")
def set_day_loss(body: DayLossRequest, request: Request):
    """Pérdida máxima del día para el conjunto de cuentas activas: al alcanzarla, kill switch y cierre de todo hasta mañana."""
    return _c(request).risk.set_day_loss(body.limit)


@router.post("/risk/auto")
async def auto_risk(body: AutoRiskRequest, request: Request):
    """Aplica el perfil de prop firm automático (firma por el nombre, tamaño por el saldo) a una cuenta o a todas las activas sin límites."""
    c = _c(request)
    if c.auto_risk is None:
        raise HTTPException(503, "riesgo automático no disponible")
    try:
        if body.account_id:
            info = await c.auto_risk.apply(body.account_id, force=body.force, firm=body.firm or None, plan=body.plan or None, size=body.size or None)
            return {"applied": [info] if info else [], "skipped": 0 if info else 1}
        applied = await c.auto_risk.apply_missing()
        return {"applied": applied, "skipped": 0}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.get("/risk/auto")
def auto_risk_status(request: Request):
    c = _c(request)
    if c.auto_risk is None:
        raise HTTPException(503, "riesgo automático no disponible")
    pending = [a.account_id for a in c.accounts.all() if c.auto_risk.needs(a.account_id)]
    return {"enabled": c.auto_risk.enabled, "firm": c.settings.AUTO_RISK_FIRM, "plan": c.settings.AUTO_RISK_PLAN, "max_contracts": c.settings.AUTO_RISK_MAX_CONTRACTS,
            "buffer": c.settings.AUTO_RISK_BUFFER, "pending": pending, "applied": c.auto_risk.applied[-50:]}


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
