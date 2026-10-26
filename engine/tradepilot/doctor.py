"""Doctor: revisa la instalación y dice qué está mal y cómo arreglarlo.

    python -m tradepilot.doctor          (desde engine\\, con el .venv activado o .\\.venv\\Scripts\\python)

También lo usa la consola (GET /api/doctor) y el zip de diagnóstico. Cada comprobación devuelve nivel ok | warn | bad,
un título y un detalle con la acción. No cambia nada.
"""
import json
import os
import shutil
import socket
import sqlite3
import sys
from pathlib import Path


def _check(level: str, title: str, detail: str = "") -> dict:
    return {"level": level, "title": title, "detail": detail}


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run_checks(settings, store=None, accounts=None, replication=None, risk=None, in_engine: bool = False) -> list[dict]:
    """`in_engine=True` cuando lo llama el propio engine (el puerto de la API lo ocupa él mismo)."""
    out: list[dict] = []
    from tradepilot.api.routes import weak_token
    # Python
    v = sys.version_info
    out.append(_check("ok" if v >= (3, 11) else "bad", f"Python {v.major}.{v.minor}.{v.micro}", "" if v >= (3, 11) else "Hace falta Python 3.11 o superior"))
    # .env
    env = Path(".env")
    out.append(_check("ok" if env.exists() else "warn", "engine\\.env", "existe" if env.exists() else "no existe: scripts\\run_engine.ps1 lo crea desde .env.example; pon API_TOKEN y ENGINE_MODE"))
    # modo y token
    out.append(_check("ok" if settings.ENGINE_MODE == "ninja" else "warn", f"Modo {settings.ENGINE_MODE}", "" if settings.ENGINE_MODE == "ninja" else "simulador: las cuentas Sim no son reales. Para operar de verdad pon ENGINE_MODE=ninja"))
    out.append(_check("bad" if weak_token(settings.API_TOKEN) else "ok", "API_TOKEN", "débil o de ejemplo: cámbialo por uno largo y aleatorio antes de publicar la consola" if weak_token(settings.API_TOKEN) else "fuerte"))
    out.append(_check("ok" if settings.API_READ_TOKEN else "info", "API_READ_TOKEN", "configurado" if settings.API_READ_TOKEN else "sin token de solo lectura (opcional, útil para mirar sin poder operar)"))
    if settings.API_READ_TOKEN and settings.API_READ_TOKEN == settings.API_TOKEN:
        out.append(_check("bad", "API_READ_TOKEN igual que API_TOKEN", "el token de lectura no restringe nada: usa uno distinto"))
    # puertos
    api_up = _port_open("127.0.0.1", settings.API_PORT)
    if in_engine:
        out.append(_check("ok", f"API en el puerto {settings.API_PORT}", "este engine"))
    else:
        out.append(_check("ok" if api_up else "warn", f"Engine en el puerto {settings.API_PORT}", "responde (hay un engine arrancado)" if api_up else "nadie escucha: el engine no está arrancado (scripts\\run_engine.ps1 o la tarea TradePilotX Engine)"))
    if settings.ENGINE_MODE == "ninja":
        sync_up = _port_open(settings.ZMQ_HOST, settings.ZMQ_SYNC_PORT)
        out.append(_check("ok" if sync_up else "bad", f"Addon de NinjaTrader (puerto {settings.ZMQ_SYNC_PORT})", "responde" if sync_up else "no responde: NinjaTrader cerrado o addon TradePilotXBridge no compilado/cargado (F5 en el editor NinjaScript)"))
    # consola
    dist = Path(settings.WEB_DIST)
    if not dist.is_absolute():
        dist = (Path(__file__).resolve().parents[1] / dist).resolve()
    idx = dist / "index.html"
    if idx.exists():
        import time
        age_d = (time.time() - idx.stat().st_mtime) / 86400
        out.append(_check("ok" if age_d < 30 else "warn", "Consola web compilada", f"hace {age_d:.0f} días" + ("" if age_d < 30 else ": ejecuta scripts\\update.ps1 para tener la última")))
    else:
        out.append(_check("bad", "Consola web sin compilar", "no existe web\\dist\\index.html: instala Node.js y ejecuta scripts\\update.ps1"))
    # base de datos
    db = Path(settings.DB_PATH)
    if settings.DB_PATH == ":memory:":
        out.append(_check("warn", "Base de datos en memoria", "se pierde al parar (solo pruebas)"))
    elif db.exists():
        size = db.stat().st_size
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            res = con.execute("PRAGMA quick_check").fetchone()[0]
            con.close()
        except Exception as exc:
            res = f"error: {exc}"
        out.append(_check("ok" if res == "ok" else "bad", f"Base de datos ({size / 1048576:.1f} MB)", "íntegra" if res == "ok" else f"dañada ({res[:80]}): para el engine y restaura una copia de data\\backups"))
        bdir = db.parent / "backups"
        files = sorted(bdir.glob("tradepilot-*.db")) if bdir.exists() else []
        if files:
            import time
            age_h = (time.time() - files[-1].stat().st_mtime) / 3600
            out.append(_check("ok" if age_h < 48 else "warn", f"Copias de seguridad: {len(files)}", f"última hace {age_h:.0f} h" + ("" if age_h < 48 else ": el engine hace una al día cuando está arrancado")))
        else:
            out.append(_check("warn", "Sin copias de seguridad", "se hace la primera a los 30 s de arrancar el engine (o pulsa Copia ahora en Riesgo)"))
        free = shutil.disk_usage(db.parent if db.parent.exists() else ".").free / 1073741824
        out.append(_check("ok" if free > 2 else "bad", f"Espacio libre en disco: {free:.1f} GB", "" if free > 2 else "menos de 2 GB: la base de datos y los logs pueden fallar"))
    else:
        out.append(_check("info", "Base de datos aún no creada", f"se crea al arrancar en {db}"))
    # logs
    logs = Path("logs")
    if logs.exists():
        total = sum(f.stat().st_size for f in logs.glob("*") if f.is_file()) / 1048576
        out.append(_check("ok" if total < 500 else "warn", f"Logs: {total:.0f} MB en engine\\logs", "" if total < 500 else "más de 500 MB: se rotan solos a 100 MB por día y 10 días; puedes borrar los antiguos"))
    # dependencias opcionales
    for mod, label, hint in (("pywebpush", "Avisos push (pywebpush)", "ejecuta scripts\\update.ps1 para instalarlo"), ("anthropic", "Cliente de IA (anthropic)", "ejecuta scripts\\update.ps1")):
        try:
            __import__(mod)
            out.append(_check("ok", label, "instalado"))
        except ImportError:
            out.append(_check("warn", label, f"no instalado: {hint}"))
    out.append(_check("ok" if settings.ANTHROPIC_API_KEY else "info", "Clave de IA", "configurada" if settings.ANTHROPIC_API_KEY else "sin ANTHROPIC_API_KEY: noticias, coach y trailing IA funcionan por reglas"))
    # reglas, límites y cuentas (si hay base de datos o servicios)
    try:
        rules = replication.rules if replication is not None else (store.get_all_rules() if store is not None else [])
        known = {a.account_id.lower() for a in accounts.accounts.values()} if accounts is not None else {a["account_id"].lower() for a in (store.get_accounts() if store is not None else [])}
        if rules:
            missing = sorted({r.follower_account for r in rules if r.enabled and known and r.follower_account.lower() not in known} | {r.master_account for r in rules if r.enabled and known and r.master_account.lower() not in known})
            out.append(_check("warn" if missing else "ok", f"Reglas de copia: {len(rules)}", f"apuntan a cuentas que NinjaTrader no ha reportado nunca: {', '.join(missing)}" if missing else "todas apuntan a cuentas conocidas"))
            limits = {l.account_id.lower() for l in (risk.limits.values() if risk is not None else (store.get_risk_limits() if store is not None else []))}
            without = sorted({r.follower_account for r in rules if r.enabled and r.follower_account.lower() not in limits})
            out.append(_check("warn" if without else "ok", "Límites de riesgo en las seguidoras", f"sin límites: {', '.join(without)} (Riesgo → aplicar perfil o preset)" if without else "todas tienen límites"))
        else:
            out.append(_check("info", "Sin reglas de copia", "crea la primera en Cuentas → vincular"))
    except Exception as exc:
        out.append(_check("warn", "No se pudieron revisar reglas y límites", str(exc)[:120]))
    # URL pública
    pub = Path(settings.PUBLIC_URL_FILE)
    if settings.PUBLIC_URL.strip() or pub.exists():
        out.append(_check("ok", "Consola publicada (túnel)", settings.PUBLIC_URL.strip() or pub.read_text(encoding="utf-8").strip().splitlines()[0] if pub.exists() else settings.PUBLIC_URL))
    else:
        out.append(_check("info", "Consola no publicada en Internet", "scripts\\publish.ps1 si quieres acceder desde fuera de casa"))
    return out


def summary(checks: list[dict]) -> dict:
    counts = {"ok": 0, "warn": 0, "bad": 0, "info": 0}
    for c in checks:
        counts[c["level"]] = counts.get(c["level"], 0) + 1
    return counts


def main() -> int:
    from tradepilot.core.config import settings
    from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
    store = None
    if settings.DB_PATH != ":memory:" and Path(settings.DB_PATH).exists():
        try:
            store = SQLiteStore(settings.DB_PATH)
        except Exception:
            store = None
    checks = run_checks(settings, store=store)
    icon = {"ok": "✔", "warn": "!", "bad": "✖", "info": "·"}
    print("TradePilot X · doctor\n")
    for c in checks:
        print(f" {icon[c['level']]} {c['title']}" + (f" — {c['detail']}" if c["detail"] else ""))
    s = summary(checks)
    print(f"\n{s['ok']} bien · {s['warn']} avisos · {s['bad']} problemas")
    if os.environ.get("DOCTOR_JSON"):
        print(json.dumps(checks, ensure_ascii=False, indent=1))
    return 1 if s["bad"] else 0


if __name__ == "__main__":
    sys.exit(main())
