"""Zip de diagnóstico en un clic: todo lo que hace falta para entender un problema, sin secretos.

Contiene: resumen (versiones, configuración con los secretos tachados, salud, riesgo, vigilante, copias, hub, última
reconciliación, contadores), cuentas, reglas y límites, auditoría de las últimas 24 h, órdenes manuales del día, el
informe del doctor y los dos últimos ficheros de log (últimos 2 MB de cada uno) con tokens y claves sustituidos por ***.
"""
import io
import json
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

MASK = "***"
SECRET_KEYS = ("API_TOKEN", "API_READ_TOKEN", "ANTHROPIC_API_KEY", "HUB_TOKEN")


def _secrets(c) -> list[str]:
    vals = [getattr(c.settings, k, "") for k in SECRET_KEYS]
    if c.notify is not None:
        vals += [c.notify.config.bot_token, c.notify.config.chat_id]
    return [v for v in vals if isinstance(v, str) and len(v) >= 4]


def redact(text: str, secrets: list[str]) -> str:
    for s in sorted(secrets, key=len, reverse=True):
        text = text.replace(s, MASK)
    # tokens que se cuelan en URLs o cabeceras
    text = re.sub(r"(token=)[^&\s\"']+", r"\1" + MASK, text)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1" + MASK, text)
    text = re.sub(r"(bot)\d{6,}:[A-Za-z0-9_\-]+", r"\1" + MASK, text)
    return text


def _settings_redacted(c) -> dict:
    out = {}
    for k, v in c.settings.model_dump().items():
        if k in SECRET_KEYS or "TOKEN" in k or "KEY" in k:
            out[k] = (MASK if v else "")
        else:
            out[k] = v
    return out


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)


def build_zip(c) -> bytes:
    secrets = _secrets(c)
    buf = io.BytesIO()
    now = datetime.now().astimezone()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        def add(name: str, text: str) -> None:
            z.writestr(name, redact(text, secrets))
        import platform
        import sys
        from tradepilot import doctor
        summary = {"generated_at": now.isoformat(timespec="seconds"), "python": sys.version, "platform": platform.platform(), "engine_version": _engine_version(),
                   "settings": _settings_redacted(c), "bridge": c.accounts.health().model_dump(mode="json"), "risk": c.risk.state().model_dump(mode="json"),
                   "watchdog": c.watchdog.state() if c.watchdog is not None else None, "backup": c.backup.state() if c.backup is not None else None,
                   "hub": c.hub.state() if c.hub is not None else None, "reconcile": c.reconcile.last if c.reconcile is not None else None,
                   "replication_stats": c.replication.stats, "trade_stats": c.trade.stats if c.trade is not None else None,
                   "notify_stats": c.notify.stats if c.notify is not None else None, "push": c.push.state() if c.push is not None else None,
                   "public_url": _public_url(c)}
        add("summary.json", _dump(summary))
        add("accounts.json", _dump([a.model_dump(mode="json") for a in c.accounts.all()]))
        add("rules.json", _dump([r.model_dump(mode="json") for r in c.replication.rules]))
        since = datetime.now() - timedelta(hours=24)
        audits = [a.model_dump(mode="json") for a in c.audit.recent(2000) if a.timestamp.replace(tzinfo=None) >= since]
        add("audit_24h.json", _dump(audits))
        if c.trade is not None:
            add("manual_orders_today.json", _dump(c.trade.recent_orders(200)))
        checks = doctor.run_checks(c.settings, store=c.store, accounts=c.accounts, replication=c.replication, risk=c.risk, in_engine=True)
        add("doctor.json", _dump(checks))
        add("doctor.txt", "\n".join(f"[{x['level']}] {x['title']}" + (f" — {x['detail']}" if x["detail"] else "") for x in checks))
        logs = sorted(Path("logs").glob("tradepilot_*.log"))[-2:] if Path("logs").exists() else []
        for f in logs:
            try:
                data = f.read_bytes()[-2 * 1048576:]
                add(f"logs/{f.name}", data.decode("utf-8", "replace"))
            except OSError:
                pass
        sup = Path("logs/supervisor.log")
        if sup.exists():
            add("logs/supervisor.log", sup.read_text(encoding="utf-8", errors="replace")[-200_000:])
        env = Path(".env")
        if env.exists():
            keys = []
            for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    keys.append(f"{k.strip()}={'(vacío)' if not v.strip() else MASK if ('TOKEN' in k or 'KEY' in k) else v.strip()}")
            add("env_keys.txt", "\n".join(keys))
    return buf.getvalue()


def _engine_version() -> str:
    try:
        from importlib.metadata import version
        return version("tradepilot-engine")
    except Exception:
        return "?"


def _public_url(c) -> str | None:
    try:
        from tradepilot.api.routes import public_url
        return public_url(c)
    except Exception:
        return None
