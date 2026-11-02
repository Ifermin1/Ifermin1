"""Etapas: empezar de cero sin perder nada. Una etapa tiene nombre, fecha de inicio y modo:

- **date**: las estadísticas (calendario, análisis, informes, coach, Inicio) solo cuentan desde la fecha; todas las cuentas.
- **new_accounts**: además solo las cuentas que NO existían al empezar la etapa (las anteriores quedan fuera; se pueden
  incluir o excluir a mano).

El recorte se hace en el almacén (`store.stage_filter`): cualquier lectura de historial lo respeta salvo dentro de
`stage_bypass()` (la petición `?scope=all` de "ver todo", y las reglas de prop firm, que necesitan el historial completo).
Nada se borra al empezar una etapa; **borrar el historial anterior** es una acción aparte, con copia de seguridad previa
(`reset_history`). Todo queda en kv `stage.config` y en la auditoría (STAGE_START / STAGE_UPDATE / STAGE_END / HISTORY_PURGED).
"""
import json
from datetime import date, datetime

MODES = ("date", "new_accounts")


class StageService:
    def __init__(self, store, accounts, audit, backup=None) -> None:
        self.store, self.accounts, self.audit, self.backup = store, accounts, audit, backup
        self.now = datetime.now
        self.config: dict = {"active": False, "name": "", "mode": "date", "start_date": "", "started_at": None, "previous": [], "include": [], "exclude": []}
        raw = store.get_kv("stage.config") if store else None
        if raw:
            try:
                self.config.update({k: v for k, v in json.loads(raw).items() if k in self.config})
            except ValueError:
                pass
        if store is not None:
            store.stage_filter = self.filter

    # ---- recorte que aplica el almacén ----
    def filter(self):
        if not self.config["active"]:
            return None, None
        start = self.config["start_date"] or None
        return start, (self.in_scope if self.config["mode"] == "new_accounts" else None)

    def in_scope(self, account_id: str | None) -> bool:
        if not self.config["active"] or self.config["mode"] != "new_accounts":
            return True
        acc = str(account_id or "")
        if acc in self.config["exclude"]:
            return False
        return acc in self.config["include"] or acc not in self.config["previous"]

    # ---- ciclo de vida ----
    def _save(self) -> None:
        if self.store:
            self.store.set_kv("stage.config", json.dumps(self.config))

    def _known_accounts(self) -> list[str]:
        ids = set(self.accounts.accounts.keys()) if self.accounts is not None else set()
        if self.store:
            try:
                ids |= {str(r["account_id"]) for r in self.store.get_accounts()}
                ids |= set(self.store.history_accounts())    # cuentas con historial aunque ya no estén en NinjaTrader
            except Exception:
                pass
        return sorted(ids)

    def start(self, name: str, mode: str = "date", start_date: str | None = None) -> dict:
        if mode not in MODES:
            raise ValueError("modo no válido: date o new_accounts")
        day = start_date or self.now().strftime("%Y-%m-%d")
        date.fromisoformat(day)
        self.config.update({"active": True, "name": (name or "Etapa").strip()[:40], "mode": mode, "start_date": day,
                            "started_at": self.now().astimezone().isoformat(timespec="seconds"),
                            "previous": self._known_accounts() if mode == "new_accounts" else [], "include": [], "exclude": []})
        self._save()
        self.audit.log("STAGE_START", f"Etapa «{self.config['name']}» desde {day}: " + ("solo cuentas nuevas a partir de ahora" if mode == "new_accounts" else "todas las cuentas desde esa fecha")
                       + ". El historial anterior no se borra: se oculta de las estadísticas (Ver todo lo muestra).", details={"mode": mode, "start_date": day, "previous": self.config["previous"]})
        return self.state()

    def update(self, **changes) -> dict:
        allowed = {"name", "mode", "start_date", "include", "exclude"}
        for k, v in changes.items():
            if k not in allowed or v is None:
                continue
            if k == "mode" and v not in MODES:
                raise ValueError("modo no válido")
            if k == "start_date":
                date.fromisoformat(v)
            if k in ("include", "exclude"):
                v = sorted({str(x) for x in v})
            if k == "name":
                v = str(v).strip()[:40]
            self.config[k] = v
        if self.config["mode"] == "new_accounts" and not self.config["previous"] and self.config["active"]:
            self.config["previous"] = self._known_accounts()
        self._save()
        self.audit.log("STAGE_UPDATE", f"Etapa «{self.config['name']}» ajustada: modo {self.config['mode']}, desde {self.config['start_date']}, incluidas {len(self.config['include'])}, excluidas {len(self.config['exclude'])}")
        return self.state()

    def stop(self) -> dict:
        if self.config["active"]:
            self.config["active"] = False
            self._save()
            self.audit.log("STAGE_END", f"Etapa «{self.config['name']}» terminada: las estadísticas vuelven a contar todo el historial")
        return self.state()

    # ---- borrar el historial anterior (con copia previa) ----
    def reset_history(self, before: str, confirm: str) -> dict:
        if confirm != "BORRAR":
            raise ValueError('escribe BORRAR para confirmar')
        date.fromisoformat(before)
        backup = None
        if self.backup is not None:
            try:
                backup = self.backup.backup_now("antes de borrar el historial")
            except RuntimeError:
                backup = None
        from tradepilot.core.scope import stage_bypass
        with stage_bypass():
            deleted = self.store.purge_before(before) if self.store else {}
        self.audit.log("HISTORY_PURGED", f"Historial anterior a {before} borrado: " + ", ".join(f"{k} {v}" for k, v in deleted.items())
                       + (f". Copia previa: {backup.get('file')}" if backup and backup.get("file") else ". Sin copia previa (base en memoria)"),
                       details={"before": before, "deleted": deleted, "backup": backup})
        return {"before": before, "deleted": deleted, "backup": backup}

    # ---- estado para la consola ----
    def state(self) -> dict:
        from tradepilot.core.scope import stage_bypass
        c = dict(self.config)
        accs = []
        for acc in self._known_accounts():
            snap = self.accounts.accounts.get(acc) if self.accounts is not None else None
            accs.append({"account_id": acc, "alias": getattr(snap, "alias", "") or "", "enabled": bool(getattr(snap, "enabled", True)),
                         "is_new": acc not in c["previous"], "in_scope": self.in_scope(acc)})
        counts = {"trades_stage": None, "trades_total": None}
        if self.store:
            try:
                today = self.now().strftime("%Y-%m-%d")
                with stage_bypass():
                    counts["trades_total"] = len(self.store.get_trades("0000-00-00", today))
                counts["trades_stage"] = len(self.store.get_trades(c["start_date"] or "0000-00-00", today)) if c["active"] else counts["trades_total"]
            except Exception:
                pass
        return {**c, "accounts": accs, "counts": counts}

    def summary(self) -> dict | None:
        """Para /api/health: solo si hay etapa activa."""
        if not self.config["active"]:
            return None
        return {"name": self.config["name"], "mode": self.config["mode"], "start_date": self.config["start_date"]}
