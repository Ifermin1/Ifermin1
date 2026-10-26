"""Reconciliación: al arrancar (tras la primera sincronización con NinjaTrader) y a petición, contrasta lo que el engine cree
con lo que el bróker reporta y deja un informe con lo que no cuadra:

- órdenes manuales que quedaron "enviando" o "sin confirmar" cuando el engine se apagó;
- posiciones sin stop en cuentas activas;
- entradas pendientes (órdenes vivas que abrirían o aumentarían posición);
- seguidoras desincronizadas respecto a la maestra;
- trailings activos cuya posición ya no existe.

No toca nada por sí misma: cada hallazgo lleva la acción sugerida (proteger, igualar, revisar) y la consola la ofrece con
un botón. El resumen queda en la auditoría (RECONCILE) para que se vea aunque nadie esté mirando.
"""
from datetime import datetime

from loguru import logger


class ReconcileService:
    def __init__(self, accounts, sync, trade=None, trail=None, audit=None, bridge=None) -> None:
        self.accounts, self.sync, self.trade, self.trail, self.audit, self.bridge = accounts, sync, trade, trail, audit, bridge
        self.last: dict | None = None
        self.startup_done = False

    async def run(self, reason: str = "manual") -> dict:
        findings: list[dict] = []
        master = (self.bridge.health.master_account or "").lower() if self.bridge is not None else ""
        # 1) órdenes manuales que se quedaron a medias
        if self.trade is not None and self.trade.store is not None:
            for o in self.trade.store.manual_orders_inflight():
                oid = o.get("order_id") or ""
                snap = self.accounts.find(o["account"])
                alive = bool(oid) and snap is not None and any(w.order_id == oid for w in snap.working_orders)
                if alive:
                    self.trade.store.manual_order_set_status(o["client_id"], "accepted")
                    continue
                self.trade.store.manual_order_set_status(o["client_id"], "unconfirmed")
                findings.append({"kind": "unconfirmed_order", "severity": "warn", "account": o["account"], "symbol": o["symbol"],
                                 "detail": f"{o['action']} {o['quantity']} {o['symbol']} {o['order_type']} quedó sin confirmar al apagarse el engine: comprueba en NinjaTrader si se ejecutó",
                                 "action": "review", "client_id": o["client_id"]})
        for snap in self.accounts.accounts.values():
            if not snap.enabled:
                continue
            # 2) posiciones sin stop
            for p in snap.open_positions:
                if not p.quantity:
                    continue
                is_long = p.quantity > 0
                stops = [w for w in snap.working_orders if w.symbol.lower() == p.symbol.lower() and w.order_type in ("STOPMARKET", "STOPLIMIT")
                         and ((is_long and w.action == "SELL") or (not is_long and w.action == "BUY"))]
                if not stops:
                    findings.append({"kind": "naked_position", "severity": "bad", "account": snap.account_id, "symbol": p.symbol,
                                     "detail": f"{'Largo' if is_long else 'Corto'} {abs(p.quantity)} {p.symbol} @ {p.avg_price:g} sin stop", "action": "protect"})
                # 5) trailing sin posición lo detecta el propio TrailService; aquí lo contrario: posición con trailing registrado pero sin stop vivo ya está arriba
            # 3) entradas pendientes
            for w in snap.working_orders:
                pos = next((p for p in snap.open_positions if p.symbol.lower() == w.symbol.lower() and p.quantity), None)
                is_exit = pos is not None and ((pos.quantity > 0 and w.action == "SELL") or (pos.quantity < 0 and w.action == "BUY"))
                if not is_exit and w.master_order_id == "":
                    findings.append({"kind": "pending_entry", "severity": "info", "account": snap.account_id, "symbol": w.symbol,
                                     "detail": f"{w.action} {w.quantity} {w.symbol} {w.order_type}{' @ ' + format(w.limit_price, 'g') if w.limit_price else ''}{' stop ' + format(w.stop_price, 'g') if w.stop_price else ''} pendiente de ejecutar",
                                     "action": "review", "order_id": w.order_id})
            # 4) desincronización
            if snap.account_id.lower() != master:
                d = self.sync.diff(snap.account_id) if self.sync is not None else {}
                for root, (exp, real) in d.items():
                    findings.append({"kind": "desync", "severity": "bad", "account": snap.account_id, "symbol": root,
                                     "detail": f"{root}: la maestra implica {exp:+d} y la cuenta tiene {real:+d}", "action": "resync"})
        # 5) trailings sin posición
        if self.trail is not None:
            for t in self.trail.list():
                snap = self.accounts.find(t["account"])
                has = snap is not None and any(p.symbol.lower() == t["symbol"].lower() and p.quantity for p in snap.open_positions)
                if not has:
                    findings.append({"kind": "trail_without_position", "severity": "info", "account": t["account"], "symbol": t["symbol"],
                                     "detail": f"trailing {t['mode']} registrado sin posición abierta (se cerrará solo)", "action": "review"})
        counts: dict[str, int] = {}
        for f in findings:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        self.last = {"ran_at": datetime.now().astimezone().isoformat(timespec="seconds"), "reason": reason, "findings": findings, "counts": counts,
                     "accounts": sum(1 for s in self.accounts.accounts.values() if s.enabled)}
        if self.audit is not None:
            if findings:
                self.audit.log("RECONCILE", f"Reconciliación ({reason}): {len(findings)} hallazgo{'s' if len(findings) != 1 else ''}: "
                               + "; ".join(f"{f['account']} {f['detail']}" for f in findings[:6]) + (" …" if len(findings) > 6 else ""),
                               details={"counts": counts, "findings": findings[:50]})
            elif reason == "arranque":
                self.audit.log("RECONCILE", "Reconciliación al arrancar: todo cuadra (posiciones protegidas, sin órdenes a medias ni desincronizaciones)")
        logger.info(f"Reconciliación ({reason}): {len(findings)} hallazgos")
        return self.last

    async def after_sync(self) -> None:
        """La llama el contenedor tras cada sincronización: la primera vez que hay cuentas, reconcilia el arranque."""
        if self.startup_done or not self.accounts.accounts:
            return
        self.startup_done = True
        await self.run("arranque")
