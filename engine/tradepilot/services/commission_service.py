import json
from datetime import datetime, timedelta

from loguru import logger

from tradepilot.domain.risk import Commissions
from tradepilot.infrastructure.persistence.sqlite_store import SQLiteStore


class CommissionService:
    """Comisiones del día por cuenta, sumadas contrato a contrato con cada ejecución que ve el engine (maestra,
    seguidoras y fills manuales). NinjaTrader no las conoce en cuentas de prop firm salvo que se configure una plantilla,
    así que el P&L que reporta es bruto; con esto el objetivo de ganancia y el límite de pérdida se miden en neto."""

    def __init__(self, store: SQLiteStore | None, eod_time: str = "17:00") -> None:
        self.store = store
        self.eod_time = eod_time
        self.now = datetime.now                       # inyectable en pruebas
        raw = store.get_kv("commissions") if store else None
        self.config = Commissions.model_validate(json.loads(raw)) if raw else Commissions()
        self._day: str = ""
        self._today: dict[str, tuple[int, float]] = {}   # cuenta -> (contratos, coste)

    def day_key(self, now: datetime | None = None) -> str:
        now = now or self.now()
        day = now.date() if now.strftime("%H:%M") >= self.eod_time else now.date() - timedelta(days=1)
        return day.isoformat()

    def _roll(self) -> None:
        day = self.day_key()
        if day != self._day:
            self._day = day
            self._today = self.store.get_commissions(day) if self.store else {}

    def rate(self, symbol: str) -> float:
        root = symbol.split(" ")[0].upper()
        return float(self.config.rates.get(root, self.config.default_per_side))

    def note_fill(self, account: str, symbol: str, quantity: int) -> None:
        if quantity <= 0:
            return
        self._roll()
        contracts, cost = self._today.get(account, (0, 0.0))
        contracts += quantity
        cost = round(cost + quantity * self.rate(symbol), 2)
        self._today[account] = (contracts, cost)
        if self.store:
            try:
                self.store.save_commission(account, self._day, contracts, cost)
            except Exception as exc:
                logger.warning(f"No se pudo guardar la comisión de {account}: {exc}")

    def today(self, account: str) -> tuple[int, float]:
        """(contratos ejecutados hoy, comisiones del día). Con las comisiones desactivadas el coste es 0."""
        self._roll()
        contracts, cost = self._today.get(account, (0, 0.0))
        return contracts, (cost if self.config.enabled else 0.0)

    def set_config(self, cfg: Commissions) -> Commissions:
        cfg.rates = {k.strip().upper(): float(v) for k, v in cfg.rates.items() if k.strip()}
        self.config = cfg
        if self.store:
            self.store.set_kv("commissions", cfg.model_dump_json())
        return cfg
