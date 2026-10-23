"""Mercado en memoria a partir de los ticks del addon (PRICE): último precio, velas de 1 minuto (OHLCV) y medidas de
volatilidad y actividad. Lo usan el trailing stop adaptativo e IA y la reacción a noticias.

Sin volumen en el tick (addon < 2.9) cada tick cuenta como 1 contrato: las razones de volumen siguen sirviendo como
medida relativa de actividad.
"""
from collections import deque
from datetime import datetime

from tradepilot.core.events import TOPIC_PRICE, EventBus
from tradepilot.domain.symbols import symbol_root, tick_size


class Bar:
    __slots__ = ("minute", "open", "high", "low", "close", "volume", "ticks")

    def __init__(self, minute: int, px: float) -> None:
        self.minute, self.open, self.high, self.low, self.close, self.volume, self.ticks = minute, px, px, px, px, 0.0, 0

    def as_dict(self) -> dict:
        return {"t": datetime.fromtimestamp(self.minute * 60).strftime("%H:%M"), "o": self.open, "h": self.high, "l": self.low, "c": self.close, "v": round(self.volume)}


class MarketService:
    def __init__(self, bus: EventBus | None = None, keep_bars: int = 240) -> None:
        self.keep = keep_bars
        self.now = datetime.now
        self.last: dict[str, float] = {}
        self.last_at: dict[str, float] = {}
        self.symbol_of: dict[str, str] = {}          # raíz -> símbolo completo visto
        self.bars: dict[str, deque] = {}
        if bus is not None:
            bus.subscribe(TOPIC_PRICE, self._on_price)

    async def _on_price(self, data: dict) -> None:
        try:
            self.on_tick(str(data.get("symbol") or ""), float(data.get("last") or 0), float(data.get("vol") or 1) or 1.0)
        except (TypeError, ValueError):
            pass

    def on_tick(self, symbol: str, last: float, vol: float = 1.0, ts: float | None = None) -> None:
        if not symbol or last <= 0:
            return
        root = symbol_root(symbol)
        t = ts if ts is not None else self.now().timestamp()
        self.last[root], self.last_at[root], self.symbol_of[root] = last, t, symbol
        q = self.bars.setdefault(root, deque(maxlen=self.keep))
        minute = int(t // 60)
        if not q or q[-1].minute != minute:
            q.append(Bar(minute, last))
        b = q[-1]
        b.high, b.low, b.close = max(b.high, last), min(b.low, last), last
        b.volume += vol
        b.ticks += 1

    # ---- consultas ----
    def price(self, symbol: str) -> float | None:
        return self.last.get(symbol_root(symbol))

    def age_s(self, symbol: str) -> float | None:
        t = self.last_at.get(symbol_root(symbol))
        return None if t is None else max(0.0, self.now().timestamp() - t)

    def recent_bars(self, symbol: str, n: int = 30, closed_only: bool = False) -> list[Bar]:
        q = list(self.bars.get(symbol_root(symbol), []))
        if closed_only and q:
            q = q[:-1]
        return q[-n:]

    def atr(self, symbol: str, n: int = 14) -> float | None:
        """Rango medio real de las últimas n velas de 1 minuto cerradas (en puntos). None con menos de 3 velas."""
        bars = self.recent_bars(symbol, n + 1, closed_only=True)
        if len(bars) < 3:
            return None
        trs = []
        for prev, b in zip(bars, bars[1:]):
            trs.append(max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close)))
        return sum(trs) / len(trs) if trs else None

    def atr_ticks(self, symbol: str, n: int = 14) -> float | None:
        a = self.atr(symbol, n)
        return None if a is None else a / tick_size(symbol)

    def volume_ratio(self, symbol: str, n: int = 20) -> float | None:
        """Volumen del último minuto completo frente a la media de los n anteriores (>1 = más actividad de lo normal)."""
        bars = self.recent_bars(symbol, n + 1, closed_only=True)
        if len(bars) < 4:
            return None
        base = [b.volume for b in bars[:-1]]
        avg = sum(base) / len(base)
        return (bars[-1].volume / avg) if avg > 0 else None

    def swing(self, symbol: str, n: int = 10) -> tuple[float, float] | None:
        """Máximo y mínimo de las últimas n velas (incluida la actual): referencia de estructura para el stop."""
        bars = self.recent_bars(symbol, n)
        if not bars:
            return None
        return max(b.high for b in bars), min(b.low for b in bars)

    def snapshot(self, symbol: str) -> dict:
        root = symbol_root(symbol)
        return {"symbol": self.symbol_of.get(root, symbol), "last": self.price(symbol), "age_s": self.age_s(symbol), "atr_ticks": self.atr_ticks(symbol),
                "volume_ratio": self.volume_ratio(symbol), "bars": len(self.bars.get(root, []))}
