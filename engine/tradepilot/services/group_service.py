"""Grupos de líderes: una configuración completa de copia (una líder + sus seguidoras con multiplicador) guardada con nombre,
para cambiar de una a otra con un clic.

El addon de NinjaTrader publica una sola cuenta maestra a la vez (SET_MASTER), así que en cada momento hay UN grupo activo:
activar otro cambia la líder en el addon, deja en marcha las reglas de sus seguidoras y apaga las reglas de las demás.
Las reglas no se borran: el grupo anterior sigue guardado tal cual para volver a él.

kv `groups`: [{id, name, master, followers: [{account, multiplier}], created_at, updated_at}]; kv `groups.active`: id."""
from __future__ import annotations

import json
import uuid
from datetime import datetime


class GroupService:
    def __init__(self, store, accounts, replication, bridge, audit, risk=None) -> None:
        self.store, self.accounts, self.replication, self.bridge, self.audit, self.risk = store, accounts, replication, bridge, audit, risk
        self.now = datetime.now
        self.groups: list[dict] = []
        self.active_id: str = ""
        raw = store.get_kv("groups") if store else None
        if raw:
            try:
                self.groups = [g for g in json.loads(raw) if isinstance(g, dict) and g.get("id")]
            except ValueError:
                self.groups = []
        self.active_id = (store.get_kv("groups.active") if store else "") or ""

    # ---- persistencia ----
    def _save(self) -> None:
        if self.store:
            self.store.set_kv("groups", json.dumps(self.groups))
            self.store.set_kv("groups.active", self.active_id)

    def get(self, group_id: str) -> dict | None:
        return next((g for g in self.groups if g["id"] == group_id), None)

    # ---- lo que hay ahora mismo en el engine, como grupo ----
    def current(self) -> dict:
        """La líder que tiene el addon y las reglas simples (sin filtro de símbolo) encendidas hacia ella."""
        master = (self.bridge.health.master_account or "") if self.bridge is not None and getattr(self.bridge, "health", None) else ""
        followers = []
        for r in self.replication.rules:
            if master and r.master_matches(master) and r.enabled and not r.symbol_filter:
                followers.append({"account": r.follower_account, "multiplier": r.multiplier})
        followers.sort(key=lambda f: f["account"].lower())
        return {"master": master, "followers": followers}

    def _matches_current(self, g: dict) -> bool:
        cur = self.current()
        if not cur["master"] or cur["master"].strip().lower() != str(g.get("master", "")).strip().lower():
            return False
        want = {f["account"].lower(): float(f["multiplier"]) for f in g.get("followers", [])}
        have = {f["account"].lower(): float(f["multiplier"]) for f in cur["followers"]}
        return want == have

    # ---- CRUD ----
    def save(self, name: str, master: str, followers: list[dict], group_id: str | None = None) -> dict:
        name, master = name.strip(), master.strip()
        if not name:
            raise ValueError("el grupo necesita un nombre")
        if not master:
            raise ValueError("el grupo necesita una líder")
        clean: list[dict] = []
        seen: set[str] = set()
        for f in followers:
            acc = str(f.get("account", "")).strip()
            if not acc or acc.lower() == master.lower() or acc.lower() in seen:
                continue
            mult = float(f.get("multiplier", 1) or 1)
            if mult <= 0:
                raise ValueError(f"multiplicador inválido para {acc}")
            seen.add(acc.lower())
            clean.append({"account": acc, "multiplier": mult})
        now = self.now().isoformat(timespec="seconds")
        if group_id:
            g = self.get(group_id)
            if g is None:
                raise KeyError(group_id)
            g.update({"name": name, "master": master, "followers": clean, "updated_at": now})
            self.audit.log("GROUP_SAVED", f"Grupo «{name}» actualizado: líder {master}, {len(clean)} seguidoras", source=master)
        else:
            if any(x["name"].strip().lower() == name.lower() for x in self.groups):
                raise ValueError(f"ya existe un grupo llamado «{name}»")
            g = {"id": uuid.uuid4().hex[:8], "name": name, "master": master, "followers": clean, "created_at": now, "updated_at": now}
            self.groups.append(g)
            self.audit.log("GROUP_SAVED", f"Grupo «{name}» guardado: líder {master}, {len(clean)} seguidoras", source=master)
        self._save()
        return g

    def save_current(self, name: str) -> dict:
        cur = self.current()
        if not cur["master"]:
            raise ValueError("no hay líder detectada: elige una cuenta como líder antes de guardar el grupo")
        g = self.save(name, cur["master"], cur["followers"])
        self.active_id = g["id"]
        self._save()
        return g

    def delete(self, group_id: str) -> bool:
        g = self.get(group_id)
        if g is None:
            return False
        self.groups = [x for x in self.groups if x["id"] != group_id]
        if self.active_id == group_id:
            self.active_id = ""
        self._save()
        self.audit.log("GROUP_DELETED", f"Grupo «{g['name']}» eliminado (las reglas no se tocan)")
        return True

    # ---- activar ----
    async def activate(self, group_id: str) -> dict:
        """Hace líder a la del grupo (addon), enciende/crea las reglas de sus seguidoras con su multiplicador y apaga las
        reglas simples del resto de líderes. Nunca borra reglas ni toca los límites de riesgo de las cuentas."""
        g = self.get(group_id)
        if g is None:
            raise KeyError(group_id)
        master = g["master"]
        if self.bridge is not None:
            await self.bridge.set_master(master)
        wanted = {f["account"].lower(): float(f["multiplier"]) for f in g["followers"]}
        turned_off, turned_on = [], []
        for r in list(self.replication.rules):
            if r.symbol_filter:
                continue
            if r.master_matches(master):
                want = wanted.get(r.follower_account.strip().lower())
                if want is None and r.enabled:
                    self.replication.update_rule(r.id, enabled=False)
                    turned_off.append(r.follower_account)
            elif r.enabled:
                self.replication.update_rule(r.id, enabled=False)
                turned_off.append(r.follower_account)
        for f in g["followers"]:
            r = self.replication.link(master, f["account"], float(f["multiplier"]), True)
            turned_on.append(r.follower_account)
        self.active_id = group_id
        self._save()
        self.audit.log("GROUP_ACTIVATED", f"Grupo «{g['name']}» activo: líder {master} → {', '.join(turned_on) or 'sin seguidoras'}"
                       + (f"; dejan de copiar: {', '.join(sorted(set(turned_off)))}" if turned_off else ""),
                       source=master, details={"group": g["id"], "on": turned_on, "off": sorted(set(turned_off))})
        return {"group": g, "on": turned_on, "off": sorted(set(turned_off))}

    # ---- estado para la consola ----
    def state(self) -> dict:
        cur = self.current()
        active = self.get(self.active_id) if self.active_id else None
        if active is not None and not self._matches_current(active):
            drift = True        # alguien cambió la líder o las reglas a mano: el grupo guardado ya no es lo que corre
        else:
            drift = False
        return {"groups": sorted(self.groups, key=lambda g: g["name"].lower()), "active_id": self.active_id if active else "",
                "current": cur, "drift": drift,
                "note": "El addon copia una sola líder a la vez: activar un grupo cambia la líder y deja copiando solo a sus seguidoras."}

    def summary(self) -> dict | None:
        active = self.get(self.active_id) if self.active_id else None
        if active is None:
            return None
        return {"id": active["id"], "name": active["name"], "master": active["master"], "followers": len(active["followers"]), "drift": not self._matches_current(active)}
