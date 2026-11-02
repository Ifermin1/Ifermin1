"""Etapas: empezar de cero sin perder nada. Una etapa tiene nombre, fecha de inicio y modo:

- **date**: las estadísticas (calendario, análisis, informes, coach, Inicio) solo cuentan desde la fecha; todas las cuentas.
- **new_accounts**: además solo las cuentas que NO existían al empezar la etapa (las anteriores quedan fuera; se pueden
  incluir o excluir a mano).

El recorte se hace en el almacén (`store.stage_filter`): cualquier lectura de historial lo respeta salvo dentro de
`stage_bypass()` (la petición `?scope=all` de "ver todo", y las reglas de prop firm, que necesitan el historial completo).
Nada se borra al empezar una etapa; **borrar el historial anterior** es una acción aparte, con copia de seguridad previa
(`reset_history`).

**Objetivo de prueba** (`goal_usd`): a cada cuenta de la etapa se le pone el objetivo de la evaluación (`RiskLimit.profit_goal`)
con su saldo actual como saldo inicial; el riesgo la pausa y cierra sola al alcanzarlo (PROFIT_GOAL). Las cuentas que
aparezcan después lo reciben en `tick()`. Si la que lo alcanza es la **maestra**, `goal_master` decide: `flatten_all`
(cerrar todas las seguidoras y activar el kill switch), `rotate` (pasar el liderazgo a la seguidora más lejos del objetivo y
reapuntar las reglas: hay que seguir operando en esa cuenta) o `master_only` (solo la maestra). Todo queda en kv `stage.config` y en la auditoría (STAGE_START / STAGE_UPDATE / STAGE_END / HISTORY_PURGED).
"""
import json
from datetime import date, datetime

from tradepilot.domain.risk import RiskLimit

MODES = ("date", "new_accounts")
GOAL_MASTER = ("flatten_all", "rotate", "master_only")


class StageService:
    def __init__(self, store, accounts, audit, backup=None, risk=None, bridge=None, replication=None) -> None:
        self.store, self.accounts, self.audit, self.backup = store, accounts, audit, backup
        self.risk, self.bridge, self.replication = risk, bridge, replication
        self.now = datetime.now
        self.config: dict = {"active": False, "name": "", "mode": "date", "start_date": "", "started_at": None, "previous": [], "include": [], "exclude": [],
                             "goal_usd": 0.0, "goal_master": "flatten_all"}
        raw = store.get_kv("stage.config") if store else None
        if raw:
            try:
                self.config.update({k: v for k, v in json.loads(raw).items() if k in self.config})
            except ValueError:
                pass
        if store is not None:
            store.stage_filter = self.filter
        if risk is not None:
            risk.on_halt = self.on_halt

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

    # ---- objetivo de prueba por cuenta ----
    def _scoped_accounts(self) -> list:
        if self.accounts is None:
            return []
        return [a for a in self.accounts.accounts.values() if a.enabled and self.in_scope(a.account_id)]

    def _apply_goal(self, snap, goal: float) -> None:
        if self.risk is None:
            return
        lim = self.risk.limits.get(snap.account_id) or RiskLimit(account_id=snap.account_id)
        resume = lim.trading_halted and lim.halted_reason == "profit_goal" and goal <= 0
        self.risk.upsert_limit(lim.model_copy(update={"profit_goal": float(goal), "start_balance": float(snap.balance) if goal > 0 else lim.start_balance,
                                                      **({"trading_halted": False} if resume else {})}))

    def set_goal(self, goal_usd: float, goal_master: str | None = None) -> dict:
        if goal_usd < 0:
            raise ValueError("el objetivo no puede ser negativo")
        if goal_master is not None and goal_master not in GOAL_MASTER:
            raise ValueError("regla de la maestra no válida")
        self.config["goal_usd"] = float(goal_usd)
        if goal_master:
            self.config["goal_master"] = goal_master
        self._save()
        applied = []
        for snap in self._scoped_accounts():
            self._apply_goal(snap, goal_usd)
            applied.append(snap.account_id)
        how = {"flatten_all": "si la maestra llega primero se cierra todo y se para la copia", "rotate": "si la maestra llega primero el liderazgo pasa a otra cuenta",
               "master_only": "si la maestra llega primero solo se cierra la maestra"}[self.config["goal_master"]]
        self.audit.log("STAGE_GOAL_SET", (f"Objetivo de prueba +{goal_usd:,.0f} $ por cuenta aplicado a {len(applied)} cuentas (saldo inicial = saldo de ahora); cada una se cierra y pausa sola al llegar; {how}"
                                          if goal_usd > 0 else f"Objetivo de prueba quitado de {len(applied)} cuentas"), details={"goal": goal_usd, "master": self.config["goal_master"], "accounts": applied})
        return self.state()

    async def tick(self) -> None:
        """Tras cada sincronización: las cuentas nuevas de la etapa reciben el objetivo."""
        goal = float(self.config.get("goal_usd") or 0)
        if not self.config["active"] or goal <= 0 or self.risk is None:
            return
        for snap in self._scoped_accounts():
            lim = self.risk.limits.get(snap.account_id)
            if lim is None or lim.profit_goal <= 0:
                self._apply_goal(snap, goal)
                self.audit.log("STAGE_GOAL_SET", f"{snap.account_id}: objetivo de prueba +{goal:,.0f} $ aplicado (cuenta nueva en la etapa, saldo inicial {snap.balance:,.2f})", target=snap.account_id)

    def _master(self) -> str | None:
        m = self.bridge.health.master_account if self.bridge is not None else None
        if not m and self.replication is not None:
            masters = {r.master_account for r in self.replication.rules if r.enabled}
            m = next(iter(masters)) if len(masters) == 1 else None
        return m

    async def on_halt(self, account: str, reason: str) -> None:
        """El riesgo acaba de pausar y cerrar `account`. Si es la maestra y fue por el objetivo de prueba, aplica goal_master."""
        if reason != "profit_goal" or not self.config["active"] or float(self.config.get("goal_usd") or 0) <= 0:
            return
        master = self._master()
        if not master or account.lower() != master.lower():
            return
        rule = self.config["goal_master"]
        if rule == "master_only":
            self.audit.log("STAGE_GOAL_MASTER", f"La maestra {account} alcanzó el objetivo de prueba y queda cerrada y pausada. Las seguidoras siguen con sus posiciones y stops; ya no se copia nada hasta que elijas otra maestra (Cuentas → Hacer líder).", target=account)
            return
        if rule == "flatten_all":
            results = await self.risk.flatten_all(include_master=False, reason="la maestra alcanzó el objetivo de prueba")
            self.risk.set_kill_switch(True, "objetivo de prueba alcanzado en la maestra")
            self.audit.log("STAGE_GOAL_MASTER", f"La maestra {account} alcanzó el objetivo de prueba: cerradas {len(results)} seguidoras ({', '.join(results) or 'ninguna con posición'}) y copia detenida (kill switch). Para seguir con las que no llegaron: elige otra maestra en Cuentas → Hacer líder y pulsa Reanudar.",
                           target=account, details={"flattened": list(results)})
            return
        # rotate: la seguidora más lejos del objetivo pasa a ser la maestra; las reglas se reapuntan
        best, best_gain = None, None
        for snap in self._scoped_accounts():
            if snap.account_id.lower() == account.lower():
                continue
            lim = self.risk.limits.get(snap.account_id)
            if lim is None or lim.trading_halted or lim.profit_goal <= 0:
                continue
            if self.replication is None or not any(r.enabled and r.master_matches(master) and r.follower_account.lower() == snap.account_id.lower() for r in self.replication.rules):
                continue
            gain = self.risk.goal_progress(lim, snap) or 0.0
            if best is None or gain < best_gain:
                best, best_gain = snap.account_id, gain
        if best is None:
            self.risk.set_kill_switch(True, "objetivo de prueba alcanzado en la maestra y no queda ninguna seguidora a la que pasar el liderazgo")
            self.audit.log("STAGE_GOAL_MASTER", f"La maestra {account} alcanzó el objetivo de prueba y no hay seguidora activa sin objetivo cumplido: copia detenida.", target=account)
            return
        try:
            applied = await self.bridge.set_master(best)
        except Exception as exc:
            self.risk.set_kill_switch(True, "objetivo de prueba alcanzado en la maestra; no se pudo pasar el liderazgo")
            self.audit.log("STAGE_GOAL_MASTER", f"La maestra {account} alcanzó el objetivo de prueba pero NinjaTrader no aceptó a {best} como nueva maestra ({exc}): copia detenida.", target=account)
            return
        moved = 0
        for r in list(self.replication.rules):
            if not r.master_matches(master):
                continue
            if r.follower_account.lower() == applied.lower():
                self.replication.delete_rule(r.id)
            else:
                self.replication.update_rule(r.id, master_account=applied)
                moved += 1
        self.audit.log("MASTER_CHANGED", f"Cuenta maestra cambiada a {applied} (objetivo de prueba alcanzado en {account})", source=applied)
        self.audit.log("STAGE_GOAL_MASTER", f"La maestra {account} alcanzó el objetivo de prueba y queda cerrada y pausada. NUEVA MAESTRA: {applied} (a +{best_gain:,.0f} $ de su objetivo, {moved} reglas reapuntadas). OPERA EN {applied} A PARTIR DE AHORA.",
                       target=applied, details={"old_master": account, "new_master": applied, "rules": moved})

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
        goal = float(c.get("goal_usd") or 0)
        for a in accs:
            snap = self.accounts.accounts.get(a["account_id"]) if self.accounts is not None else None
            lim = self.risk.limits.get(a["account_id"]) if self.risk is not None else None
            gained = self.risk.goal_progress(lim, snap) if (lim is not None and snap is not None and lim.profit_goal > 0) else None
            a["goal"] = {"goal": lim.profit_goal if lim else 0.0, "gained": gained, "pct": round(100 * gained / lim.profit_goal, 1) if (gained is not None and lim and lim.profit_goal > 0) else None,
                         "reached": bool(lim and lim.trading_halted and lim.halted_reason == "profit_goal"), "start_balance": lim.start_balance if lim else 0.0}
        counts = {"trades_stage": None, "trades_total": None}
        if self.store:
            try:
                today = self.now().strftime("%Y-%m-%d")
                with stage_bypass():
                    counts["trades_total"] = len(self.store.get_trades("0000-00-00", today))
                counts["trades_stage"] = len(self.store.get_trades(c["start_date"] or "0000-00-00", today)) if c["active"] else counts["trades_total"]
            except Exception:
                pass
        return {**c, "accounts": accs, "counts": counts, "master": self._master()}

    def summary(self) -> dict | None:
        """Para /api/health: solo si hay etapa activa."""
        if not self.config["active"]:
            return None
        return {"name": self.config["name"], "mode": self.config["mode"], "start_date": self.config["start_date"]}
