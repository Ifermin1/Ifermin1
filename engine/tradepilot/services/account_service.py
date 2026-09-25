import asyncio
from datetime import datetime

from loguru import logger

from tradepilot.core.events import TOPIC_ACCOUNTS, TOPIC_HEALTH, EventBus
from tradepilot.domain.accounts import AccountSnapshot, BridgeHealth, DrawdownSnapshot, PositionSnapshot
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore
from tradepilot.infrastructure.brokers.base import BrokerBridge


class AccountService:
    """Sincroniza periódicamente las cuentas del bróker y publica snapshots."""

    def __init__(self, bridge: BrokerBridge, bus: EventBus, store: SQLiteStore | None = None,
                 interval: float = 2.0) -> None:
        self.bridge = bridge
        self.bus = bus
        self.store = store
        self.interval = interval
        self.after_sync = None                 # corrutina a llamar tras cada sincronización (SyncService.check)
        self.on_positions_refreshed = None     # callable(cuenta | None) tras actualizar posiciones (libro de exposición)
        self.limits_provider = lambda: {}      # {cuenta: RiskLimit} (lo inyecta el contenedor) para el drawdown dinámico
        self.eod_time = "17:00"                # hora local del cierre del día para el drawdown EOD (DRAWDOWN_EOD_TIME)
        self.commissions = None                # CommissionService (lo inyecta el contenedor)
        self.now = datetime.now                # inyectable en pruebas
        # Marca de agua por cuenta: el máximo que llegó a valer (con flotante y solo cerrado). Sobrevive a reinicios.
        self._peaks: dict[str, dict] = store.get_peaks() if store else {}
        self._watched: set[str] = set()
        self._watch_pending: set[str] = set()   # WATCH que el addon aún no confirmó: se reintenta en cada sync
        self.accounts: dict[str, AccountSnapshot] = {}
        # Cuentas recordadas de sesiones anteriores: aparecen aunque el bróker aún no las reporte
        for row in (store.get_accounts() if store else []):
            self.accounts[row["account_id"]] = AccountSnapshot(
                account_id=row["account_id"], balance=row["last_balance"] or 0.0, net_liquidity=row["last_balance"] or 0.0,
                enabled=bool(row["enabled"]), enabled_source=row["enabled_source"] or "auto",
                alias=row["alias"] or "", reported=False,
                updated_at=datetime.fromisoformat(row["last_seen"]) if row["last_seen"] else datetime.now())
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def sync_once(self) -> None:
        data = await self.bridge.get_accounts()
        now = datetime.now()
        reported = {a.account_id for a in data}
        new_hidden = 0
        for info in data:
            # Política "auto": una cuenta está activa mientras esté conectada. Si el addon no informa
            # del estado (versión antigua) todo lo reportado cuenta como conectado.
            auto_enabled = True if info.connected is None else info.connected
            snap = self.accounts.get(info.account_id)
            if snap is None:
                snap = self.accounts[info.account_id] = AccountSnapshot(account_id=info.account_id, updated_at=now,
                                                                        enabled=auto_enabled)
                if auto_enabled:
                    logger.info(f"Cuenta conectada detectada: {info.account_id} ({info.connection or 'sin conexión'})")
                else:
                    new_hidden += 1
            elif snap.enabled_source == "auto" and snap.enabled != auto_enabled:
                snap.enabled = auto_enabled
                logger.info(f"Cuenta {info.account_id} {'activada (conectada)' if auto_enabled else 'oculta (desconectada)'}")
            snap.balance = snap.net_liquidity = info.balance
            snap.realized_pnl, snap.unrealized_pnl = info.realized_pnl, info.unrealized_pnl
            snap.daily_pnl = info.realized_pnl + info.unrealized_pnl
            if self.commissions is not None:
                snap.contracts_today, snap.commissions_today = self.commissions.today(info.account_id)
            snap.net_pnl = round(snap.daily_pnl - snap.commissions_today, 2)
            if self.performance is not None and snap.enabled:
                self.performance.note_daily_pnl(info.account_id, snap.daily_pnl, self.now())
            snap.connected, snap.connection, snap.reported, snap.updated_at = info.connected, info.connection, True, now
            if self.store:
                self.store.upsert_account_seen(info.account_id, info.balance, now.isoformat(), snap.enabled, info.connected)
            self._track_drawdown(snap, self.now())
        if new_hidden:
            logger.info(f"{new_hidden} cuentas nuevas sin conexión quedan ocultas (se activan solas al conectarse)")
        import time as _t
        asked = _t.monotonic()
        positions = await self.bridge.get_positions()
        if positions is not None:
            self.apply_positions(positions, now, as_of=asked)
        asked = _t.monotonic()
        orders = await self.bridge.get_orders()
        if orders is not None:
            self.apply_orders(orders)
            self.orders_as_of = asked
        self._sample_pnl(now)
        for acc in list(self._watch_pending):
            await self.watch(acc)
        if data:
            for acc, snap in self.accounts.items():
                if acc not in reported:
                    snap.reported = False
                    snap.connected = False if snap.connected is not None else None
                    if snap.enabled_source == "auto" and snap.enabled:
                        snap.enabled = False
                        if self.store:
                            self.store.set_account_settings(acc, enabled=False)
            await self.publish_accounts()
        if self.after_sync:
            try:
                await self.after_sync()
            except Exception as exc:
                logger.error(f"Error en vigilancia de sincronización: {exc}")
        await self.bus.publish(TOPIC_HEALTH, self.health().model_dump(mode="json"))

    def forget_watches(self) -> None:
        """Tras un reinicio del addon hay que volver a pedir WATCH de cada seguidora."""
        self._watched.clear()
        self._watch_pending.clear()

    async def watch(self, account_id: str) -> None:
        """Pide al addon que reporte esa cuenta (órdenes/posiciones) aunque aún no le hayamos mandado órdenes.
        Si el addon no lo confirma (recién reiniciado), queda pendiente y se reintenta en cada sync."""
        if account_id in self._watched:
            return
        try:
            ok = await self.bridge.watch(account_id)
        except Exception as exc:
            logger.warning(f"watch {account_id}: {exc}")
            ok = False
        if ok:
            self._watched.add(account_id)
            self._watch_pending.discard(account_id)
        else:
            self._watch_pending.add(account_id)

    _last_signature: tuple | None = None
    orders_as_of: float = 0.0        # monotonic en que se pidió la última foto de órdenes vivas (GET_ORDERS)

    def find(self, account_id: str):
        """Snapshot por nombre, sin distinguir mayúsculas (el libro del replicador trabaja en minúsculas)."""
        snap = self.accounts.get(account_id)
        if snap is None:
            low = account_id.lower()
            snap = next((s for k, s in self.accounts.items() if k.lower() == low), None)
        return snap

    def apply_positions(self, positions: list, now: datetime, as_of: float | None = None) -> None:
        """Snapshot completo de posiciones del bróker (GET_POSITIONS): sustituye lo que teníamos. `as_of` es el monotonic
        en que se pidió la foto: un fill posterior a ese instante no está reflejado en ella."""
        if self.performance is not None:
            self.performance.seed_open_positions(positions)
        by_acc: dict[str, list[PositionSnapshot]] = {}
        for p in positions:
            by_acc.setdefault(p.account_id, []).append(
                PositionSnapshot(account_id=p.account_id, symbol=p.symbol, quantity=p.quantity, avg_price=p.avg_price))
        for acc, snap in self.accounts.items():
            new = by_acc.get(acc, [])
            if [(x.symbol, x.quantity) for x in snap.open_positions] != [(x.symbol, x.quantity) for x in new]:
                snap.open_positions = new
                snap.updated_at = now
        if self.on_positions_refreshed:
            self.on_positions_refreshed(None, as_of)

    audit = None                 # AuditService, lo inyecta el contenedor
    performance = None           # PerformanceService (calendario), lo inyecta el contenedor
    _phantoms_seen: set = set()  # (cuenta, id de orden) ya avisadas

    def apply_orders(self, orders: list) -> None:
        """Snapshot completo de órdenes vivas (GET_ORDERS): sustituye lo que teníamos."""
        by_acc: dict[str, list] = {}
        for acc, o in orders:
            by_acc.setdefault(acc, []).append(o)
            if o.quantity - o.filled <= 0 and (acc, o.order_id) not in self._phantoms_seen and self.audit is not None:
                # 16/9 14:23: apareció un "0 Sell STP" en el gráfico. Una orden con 0 contratos no protege nada; se avisa
                # una vez con todo lo que hace falta para localizarla (el addon >= 2.4 cancela solo las copias TPX).
                self._phantoms_seen.add((acc, o.order_id))
                price = o.stop_price or o.limit_price
                who = f"copia TPX de la orden maestra {o.master_order_id}" if o.master_order_id else "orden propia de la cuenta, no del copiador"
                self.audit.log("PHANTOM_ORDER", f"{acc}: orden {o.action} {o.order_type} @ {price} {o.symbol} sin nada por ejecutar "
                               f"({o.filled}/{o.quantity}, estado {o.state}; {who}): no protege nada. El addon >= 2.4 la cancela; "
                               "si no, cancélala en NinjaTrader",
                               target=acc, details={"order_id": o.order_id, "master_order_id": o.master_order_id, "state": o.state})
        for acc, snap in self.accounts.items():
            new = by_acc.get(acc, [])
            if [o.model_dump() for o in snap.working_orders] != [o.model_dump() for o in new]:
                snap.working_orders = new

    # ---- drawdown dinámico (marca de agua) ----
    def _track_drawdown(self, snap: AccountSnapshot, now: datetime) -> None:
        """Actualiza el máximo que llegó a valer la cuenta (equity = balance + flotante, y balance cerrado) y calcula
        el drawdown según el límite configurado. Es lo que mide el prop firm: si la cuenta baja `limit` desde su máximo,
        la cierra. Aquí se ve venir y, con límite, el engine actúa antes (RiskService)."""
        equity = snap.balance + snap.unrealized_pnl
        pk = self._peaks.get(snap.account_id)
        if pk is None or pk.get("peak_equity") is None:
            pk = self._peaks[snap.account_id] = {"peak_equity": equity, "peak_equity_at": now.isoformat(timespec="seconds"),
                                                 "peak_balance": snap.balance, "peak_balance_at": now.isoformat(timespec="seconds"),
                                                 "peak_eod": snap.balance, "peak_eod_day": self._eod_day(now)}
            changed = True
        else:
            changed = False
            if equity > pk["peak_equity"]:
                pk["peak_equity"], pk["peak_equity_at"], changed = equity, now.isoformat(timespec="seconds"), True
            if snap.balance > (pk.get("peak_balance") or 0.0):
                pk["peak_balance"], pk["peak_balance_at"], changed = snap.balance, now.isoformat(timespec="seconds"), True
            if pk.get("peak_eod") is None:
                pk["peak_eod"], pk["peak_eod_day"], changed = snap.balance, self._eod_day(now), True
        # EOD: al pasar la hora de cierre, el máximo EOD sube al balance de cierre si lo supera (y nunca baja)
        if self._eod_day(now) != pk.get("peak_eod_day"):
            pk["peak_eod"], pk["peak_eod_day"], changed = max(pk.get("peak_eod") or 0.0, snap.balance), self._eod_day(now), True
            logger.info(f"Drawdown EOD {snap.account_id}: cierre del día {pk['peak_eod_day']} con balance {snap.balance:,.2f}; máximo EOD {pk['peak_eod']:,.2f}")
        if changed:
            self._save_peak(snap.account_id, pk)
        self._compute_drawdown(snap, pk, equity)

    def _eod_day(self, now: datetime) -> str:
        """Día de cierre vigente: hasta la hora de cierre es el de ayer; a partir de ella, el de hoy."""
        from datetime import timedelta
        day = now.date() if now.strftime("%H:%M") >= self.eod_time else now.date() - timedelta(days=1)
        return day.isoformat()

    def _save_peak(self, account_id: str, pk: dict) -> None:
        if not self.store:
            return
        try:
            self.store.save_peak(account_id, pk["peak_equity"], pk["peak_equity_at"], pk["peak_balance"], pk["peak_balance_at"],
                                 pk.get("peak_eod"), pk.get("peak_eod_day"))
        except Exception as exc:
            logger.warning(f"No se pudo guardar el máximo de {account_id}: {exc}")

    def peak_for(self, account_id: str, mode: str) -> float | None:
        """Máximo vigente de una cuenta según el modo de drawdown (para decidir si se puede reanudar)."""
        pk = self._peaks.get(account_id)
        if pk is None:
            return None
        return pk.get("peak_eod") if mode == "eod" else pk.get("peak_balance") if mode == "closed" else pk.get("peak_equity")

    def _compute_drawdown(self, snap: AccountSnapshot, pk: dict, equity: float) -> None:
        limit = self.limits_provider().get(snap.account_id)
        mode = limit.drawdown_mode if limit else "intraday"
        closed = mode == "closed"
        if mode == "eod":
            # el suelo se fija con el balance del último cierre; la caída se mide en tiempo real con el flotante
            peak, peak_at, value = pk.get("peak_eod") or snap.balance, pk.get("peak_eod_day"), equity
            if peak_at:
                peak_at = f"{peak_at}T{self.eod_time}:00"
        else:
            peak = pk["peak_balance"] if closed else pk["peak_equity"]
            peak_at = pk.get("peak_balance_at" if closed else "peak_equity_at")
            value = snap.balance if closed else equity
        dd = DrawdownSnapshot(equity=round(equity, 2), mode=mode, peak=round(peak, 2),
                              peak_at=datetime.fromisoformat(peak_at) if peak_at else None,
                              drawdown=round(max(0.0, peak - value), 2))
        if limit and limit.max_trailing_drawdown > 0:
            floor = peak - limit.max_trailing_drawdown
            if limit.drawdown_floor_cap > 0 and floor >= limit.drawdown_floor_cap:
                floor, dd.locked = limit.drawdown_floor_cap, True
            dd.limit, dd.buffer, dd.floor = limit.max_trailing_drawdown, limit.drawdown_buffer, round(floor, 2)
            dd.room = round(value - floor, 2)
            dd.pct = round(min(100.0, max(0.0, (limit.max_trailing_drawdown - dd.room) / limit.max_trailing_drawdown * 100)), 1)
        snap.drawdown = dd

    def refresh_drawdown(self, account_id: str) -> None:
        """Recalcula el drawdown de una cuenta con el límite actual (tras cambiar los límites en la consola)."""
        snap, pk = self.accounts.get(account_id), self._peaks.get(account_id)
        if snap is not None and pk is not None:
            self._compute_drawdown(snap, pk, snap.balance + snap.unrealized_pnl)

    async def set_peak(self, account_id: str, peak: float | None) -> AccountSnapshot:
        """Fija a mano el máximo (marca de agua) de una cuenta o, con None, lo reinicia al valor actual. Útil cuando el
        prop firm tiene otro máximo (la cuenta operó sin el engine) o al empezar una evaluación nueva."""
        snap = self.accounts.get(account_id)
        if snap is None:
            raise KeyError(account_id)
        now = datetime.now().isoformat(timespec="seconds")
        equity = snap.balance + snap.unrealized_pnl
        day = self._eod_day(self.now())
        if peak is None:
            pk = {"peak_equity": equity, "peak_equity_at": now, "peak_balance": snap.balance, "peak_balance_at": now,
                  "peak_eod": snap.balance, "peak_eod_day": day}
        else:
            pk = {"peak_equity": float(peak), "peak_equity_at": now, "peak_balance": float(peak), "peak_balance_at": now,
                  "peak_eod": float(peak), "peak_eod_day": day}
        self._peaks[account_id] = pk
        self._save_peak(account_id, pk)
        self._compute_drawdown(snap, pk, equity)
        if self.audit is not None:
            self.audit.log("PEAK_SET", f"{account_id}: máximo para el drawdown {'reiniciado al valor actual' if peak is None else 'fijado a mano'}: "
                           f"{pk['peak_equity']:,.2f}" + (f" (suelo {snap.drawdown.floor:,.2f})" if snap.drawdown.floor is not None else ""),
                           target=account_id, details={"peak": pk["peak_equity"], "floor": snap.drawdown.floor})
        await self.publish_accounts(force=True)
        return snap

    pnl_sample_seconds = 15.0
    _last_sample: float = 0.0

    def _sample_pnl(self, now: datetime) -> None:
        """Curva de P&L del día: una muestra por cuenta activa cada pocos segundos (para el gráfico de la consola)."""
        if not self.store:
            return
        import time
        if time.monotonic() - self._last_sample < self.pnl_sample_seconds:
            return
        self._last_sample = time.monotonic()
        rows = [(a.account_id, now.isoformat(timespec="seconds"), a.daily_pnl) for a in self.accounts.values() if a.enabled and a.reported]
        if rows:
            try:
                self.store.add_pnl_samples(rows)
            except Exception as exc:
                logger.warning(f"No se pudo guardar la muestra de P&L: {exc}")

    def position(self, account_id: str, symbol: str) -> int:
        snap = self.accounts.get(account_id)
        if not snap:
            return 0
        root = symbol.split(" ")[0].upper()
        return sum(p.quantity for p in snap.open_positions if p.symbol.split(" ")[0].upper() == root)

    async def publish_accounts(self, force: bool = False) -> None:
        """Publica el snapshot solo si cambió algo relevante (con cientos de cuentas importa)."""
        sig = tuple((a.account_id, a.balance, a.connected, a.enabled, a.alias, a.reported, a.desync, round(a.daily_pnl),
                     round(a.drawdown.peak), a.drawdown.floor, a.drawdown.pct, a.contracts_today,
                     tuple((p.symbol, p.quantity) for p in a.open_positions),
                     tuple((o.order_id, o.quantity, o.filled, o.limit_price, o.stop_price, o.state) for o in a.working_orders))
                    for a in self.accounts.values())
        if force or sig != self._last_signature:
            self._last_signature = sig
            await self.bus.publish(TOPIC_ACCOUNTS, [a.model_dump(mode="json") for a in self.accounts.values()])

    # ---- gestión desde la consola ----
    def is_enabled(self, account_id: str) -> bool:
        snap = self.accounts.get(account_id)
        return True if snap is None else snap.enabled

    async def set_settings(self, account_id: str, enabled: bool | None = None, alias: str | None = None,
                           auto: bool = False) -> AccountSnapshot:
        """enabled fija la cuenta a mano (source=user); auto=True vuelve a la política automática."""
        snap = self.accounts.get(account_id)
        if snap is None:
            snap = self.accounts[account_id] = AccountSnapshot(account_id=account_id, reported=False, updated_at=datetime.now())
        source = None
        if auto:
            snap.enabled_source = source = "auto"
            snap.enabled = enabled = bool(snap.connected) if snap.connected is not None else snap.reported
        elif enabled is not None:
            snap.enabled = enabled
            snap.enabled_source = source = "user"
        if alias is not None:
            snap.alias = alias.strip()
        if self.store:
            self.store.set_account_settings(account_id, enabled, alias, source)
        await self.publish_accounts()
        return snap

    async def forget(self, account_id: str) -> bool:
        """Olvida una cuenta que el bróker ya no reporta."""
        snap = self.accounts.get(account_id)
        if snap is None or snap.reported:
            return False
        del self.accounts[account_id]
        self._peaks.pop(account_id, None)
        if self.store:
            self.store.delete_account(account_id)
            self.store.delete_peak(account_id)
        await self.publish_accounts()
        return True

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error sincronizando cuentas: {exc}")
            await asyncio.sleep(self.interval)

    async def update_position(self, account_id: str, symbol: str, market_position: str, quantity: int,
                              avg_price: float) -> None:
        """POSITION del addon: mantiene las posiciones abiertas de la cuenta."""
        now = datetime.now()
        snap = self.accounts.get(account_id)
        if snap is None:
            snap = self.accounts[account_id] = AccountSnapshot(account_id=account_id, updated_at=now)
        positions = [p for p in snap.open_positions if p.symbol != symbol]
        if quantity > 0 and market_position.upper() != "FLAT":
            signed = quantity if market_position.upper() == "LONG" else -quantity
            positions.append(PositionSnapshot(account_id=account_id, symbol=symbol, quantity=signed, avg_price=avg_price))
        snap.open_positions = positions
        snap.updated_at = now
        if self.on_positions_refreshed:
            self.on_positions_refreshed(account_id)
        await self.bus.publish(TOPIC_ACCOUNTS, [a.model_dump(mode="json") for a in self.accounts.values()])

    def all(self) -> list[AccountSnapshot]:
        return list(self.accounts.values())

    def health(self) -> BridgeHealth:
        return self.bridge.health
