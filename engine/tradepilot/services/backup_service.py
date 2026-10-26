"""Copias de seguridad de la base de datos y exportación/importación de la configuración.

- **Copias**: `backup_now()` copia el SQLite de forma consistente (API `sqlite3.backup`, sin parar el engine) a
  `data/backups/tradepilot-AAAAMMDD-HHMM.db` y conserva las últimas BACKUP_KEEP. El bucle hace una al día (y una al
  arrancar si la última tiene más de un día). Al arrancar también pasa `PRAGMA quick_check`: si la base está dañada lo
  audita (DB_CORRUPT) y avisa. Restaurar = parar el engine y copiar el fichero sobre `data/tradepilot.db`.
- **Configuración**: `export_config()` devuelve un JSON sin secretos (reglas, límites, presets, horario, comisiones,
  pérdida del día, ajustes de cuentas, plantillas, parámetros de análisis, qué avisos mandar) e `import_config()` lo
  aplica por las mismas funciones que usa la consola, para llevar la configuración a otro PC o recuperarla tras
  reinstalar.
"""
import asyncio
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

CONFIG_VERSION = 1


class BackupService:
    def __init__(self, store, audit, db_path: str, keep: int = 14, interval_h: float = 24.0, accounts=None, risk=None, replication=None, notify=None) -> None:
        self.store, self.audit, self.keep, self.interval_h = store, audit, max(1, int(keep)), max(0.1, float(interval_h))
        self.accounts, self.risk, self.replication, self.notify = accounts, risk, replication, notify
        self.db_path = Path(db_path) if db_path and db_path != ":memory:" else None
        self.dir = (self.db_path.parent / "backups") if self.db_path else None
        self.last_at: float | None = None
        self.last_error: str | None = None
        self.integrity: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        return self.db_path is not None

    # ---- ciclo de vida ----
    async def start(self) -> None:
        self.check_integrity()
        if self.available:
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                last = self.latest()
                age_h = (time.time() - last["mtime"]) / 3600 if last else None
                if age_h is None or age_h >= self.interval_h:
                    self.backup_now("automática")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Copia de seguridad: {exc}")
            await asyncio.sleep(3600)

    # ---- integridad ----
    def check_integrity(self) -> str:
        try:
            with self.store._lock:
                rows = self.store._conn.execute("PRAGMA quick_check").fetchall()
            res = "; ".join(str(r[0]) for r in rows)
        except Exception as exc:
            res = f"error: {exc}"
        self.integrity = res
        if res != "ok":
            logger.error(f"Base de datos dañada: {res}")
            try:
                self.audit.log("DB_CORRUPT", f"La base de datos del engine no pasa la comprobación de integridad ({res[:120]}). Para el engine y restaura la última copia de data/backups.")
            except Exception:
                pass
        return res

    # ---- copias ----
    def list(self) -> list[dict]:
        if not self.dir or not self.dir.exists():
            return []
        out = []
        for f in sorted(self.dir.glob("tradepilot-*.db"), reverse=True):
            st = f.stat()
            out.append({"name": f.name, "size": st.st_size, "mtime": st.st_mtime, "at": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds")})
        return out

    def latest(self) -> dict | None:
        files = self.list()
        return files[0] if files else None

    def backup_now(self, reason: str = "manual") -> dict:
        if not self.available:
            raise RuntimeError("base de datos en memoria: no hay nada que copiar")
        self.dir.mkdir(parents=True, exist_ok=True)
        name = f"tradepilot-{datetime.now():%Y%m%d-%H%M%S}.db"
        dest = self.dir / name
        try:
            with self.store._lock:
                out = sqlite3.connect(str(dest))
                try:
                    self.store._conn.backup(out)
                finally:
                    out.close()
        except Exception as exc:
            self.last_error = str(exc)[:200]
            self.audit.log("BACKUP_FAILED", f"No se pudo hacer la copia de seguridad: {exc}")
            raise
        self.last_at, self.last_error = time.time(), None
        removed = self._rotate()
        size = dest.stat().st_size
        self.audit.log("BACKUP", f"Copia de seguridad {reason}: {name} ({size / 1024:.0f} KB){' · borradas ' + str(removed) + ' antiguas' if removed else ''}", details={"name": name, "size": size})
        return {"name": name, "size": size, "at": datetime.now().astimezone().isoformat(timespec="seconds"), "removed": removed}

    def _rotate(self) -> int:
        files = self.list()
        removed = 0
        for f in files[self.keep:]:
            try:
                (self.dir / f["name"]).unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def path_of(self, name: str) -> Path | None:
        if not self.dir or "/" in name or "\\" in name or not name.startswith("tradepilot-") or not name.endswith(".db"):
            return None
        p = self.dir / name
        return p if p.exists() else None

    def state(self) -> dict:
        files = self.list()
        return {"available": self.available, "dir": str(self.dir) if self.dir else None, "keep": self.keep, "interval_h": self.interval_h, "integrity": self.integrity,
                "last_at": datetime.fromtimestamp(files[0]["mtime"]).astimezone().isoformat(timespec="seconds") if files else None, "last_error": self.last_error, "files": files[:30]}

    # ---- configuración ----
    def export_config(self) -> dict:
        from tradepilot.api.routes import ANALYSIS_KEY, PRESETS_KEY, TEMPLATES_KEY
        rs = self.risk.state() if self.risk is not None else None
        accounts = []
        if self.accounts is not None:
            for a in self.accounts.accounts.values():
                accounts.append({"account_id": a.account_id, "alias": a.alias, "enabled": a.enabled, "enabled_source": a.enabled_source, "firm": a.firm, "plan": a.plan, "plan_size": a.plan_size, "tags": list(a.tags)})
        kv = lambda k: json.loads(self.store.get_kv(k) or "null")  # noqa: E731
        return {"version": CONFIG_VERSION, "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "rules": [r.model_dump(mode="json") for r in (self.replication.rules if self.replication is not None else [])],
                "risk_limits": [l.model_dump(mode="json") for l in rs.limits] if rs else [],
                "schedule": rs.schedule.model_dump(mode="json") if rs else None, "commissions": rs.commissions.model_dump(mode="json") if rs else None,
                "day_loss_limit": rs.day_loss_limit if rs else 0,
                "accounts": accounts, "risk_presets": kv(PRESETS_KEY), "trade_templates": kv(TEMPLATES_KEY), "analysis_params": kv(ANALYSIS_KEY),
                "notifications": {"enabled": self.notify.config.enabled, "events": list(self.notify.config.events)} if self.notify is not None else None}

    async def import_config(self, data: dict, replace_rules: bool = False) -> dict:
        from tradepilot.api.routes import ANALYSIS_KEY, PRESETS_KEY, TEMPLATES_KEY
        from tradepilot.domain.risk import Commissions, RiskLimit, Schedule
        if not isinstance(data, dict) or int(data.get("version", 0)) > CONFIG_VERSION:
            raise ValueError("archivo de configuración no válido o de una versión más nueva")
        done = {"rules": 0, "risk_limits": 0, "accounts": 0, "other": []}
        if self.replication is not None and isinstance(data.get("rules"), list):
            if replace_rules:
                for r in list(self.replication.rules):
                    self.replication.delete_rule(r.id)
            for r in data["rules"]:
                key = (str(r.get("master_account", "")).lower(), str(r.get("follower_account", "")).lower(), (r.get("symbol_filter") or None))
                existing = next((x for x in self.replication.rules if (x.master_account.lower(), x.follower_account.lower(), x.symbol_filter or None) == key), None)
                fields = {k: r[k] for k in ("target_root", "entry_mode", "tolerance_ticks", "entry_timeout_s", "entry_fallback") if k in r}
                if existing is not None:
                    self.replication.update_rule(existing.id, multiplier=r.get("multiplier", existing.multiplier), enabled=r.get("enabled", existing.enabled), **fields)
                else:
                    self.replication.add_rule(r["master_account"], r["follower_account"], r.get("multiplier", 1.0), r.get("symbol_filter"), r.get("enabled", True), **fields)
                done["rules"] += 1
        if self.risk is not None:
            for l in data.get("risk_limits") or []:
                try:
                    self.risk.upsert_limit(RiskLimit(**l))
                    done["risk_limits"] += 1
                except (ValueError, TypeError) as exc:
                    logger.warning(f"Importar límites de {l.get('account_id')}: {exc}")
            if data.get("schedule"):
                self.risk.set_schedule(Schedule(**data["schedule"]))
                done["other"].append("horario")
            if data.get("commissions"):
                self.risk.set_commissions(Commissions(**data["commissions"]))
                done["other"].append("comisiones")
            if data.get("day_loss_limit") is not None:
                self.risk.set_day_loss(float(data["day_loss_limit"]))
                done["other"].append("pérdida del día")
        if self.accounts is not None:
            for a in data.get("accounts") or []:
                if not a.get("account_id"):
                    continue
                await self.accounts.set_settings(a["account_id"], enabled=a.get("enabled") if a.get("enabled_source") == "user" else None, alias=a.get("alias"),
                                                 firm=a.get("firm"), plan=a.get("plan"), plan_size=a.get("plan_size"), tags=a.get("tags"))
                done["accounts"] += 1
        for key, name, label in ((PRESETS_KEY, "risk_presets", "presets"), (TEMPLATES_KEY, "trade_templates", "plantillas"), (ANALYSIS_KEY, "analysis_params", "parámetros de análisis")):
            if data.get(name) is not None:
                self.store.set_kv(key, json.dumps(data[name]))
                done["other"].append(label)
        if self.notify is not None and isinstance(data.get("notifications"), dict) and isinstance(data["notifications"].get("events"), list):
            cfg = self.notify.config.model_copy(update={"events": [str(e) for e in data["notifications"]["events"]]})
            self.notify.set_config(cfg)
            done["other"].append("avisos")
        self.audit.log("CONFIG_IMPORTED", f"Configuración importada: {done['rules']} reglas, {done['risk_limits']} límites, {done['accounts']} cuentas"
                       + (", " + ", ".join(done["other"]) if done["other"] else ""), details=done)
        return done
