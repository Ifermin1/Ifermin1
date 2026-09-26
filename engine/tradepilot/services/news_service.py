"""Calendario económico: eventos de la semana (y la siguiente) con su impacto, previsión y dato anterior.

Fuente: el JSON público de ForexFactory (nfs.faireconomy.media/ff_calendar_thisweek.json y _nextweek.json), sin clave.
Se cachea 30 min; si la descarga falla se sirve lo último que se tuviera y se informa del error. En modo simulador se
generan eventos de muestra para que la pestaña se vea sin conexión.
"""
import random
import time
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from loguru import logger

URLS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json", "https://nfs.faireconomy.media/ff_calendar_nextweek.json")
COUNTRY_NAMES = {"USD": "EE. UU.", "EUR": "Eurozona", "GBP": "Reino Unido", "JPY": "Japón", "CAD": "Canadá", "AUD": "Australia",
                 "NZD": "N. Zelanda", "CHF": "Suiza", "CNY": "China", "ALL": "Global"}
IMPACTS = {"high": 3, "medium": 2, "low": 1, "holiday": 0}


class NewsService:
    def __init__(self, fetcher: Callable[[str], Awaitable[list]] | None = None, ttl_s: float = 1800.0) -> None:
        self.fetcher = fetcher or self._http
        self.ttl_s = ttl_s
        self.now = datetime.now
        self._events: list[dict] = []
        self._fetched_at: float | None = None
        self._error: str | None = None
        self._lock_until = 0.0

    # ---- descarga ----
    @staticmethod
    async def _http(url: str) -> list:
        import httpx
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "TradePilotX/1.0"}) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []

    @staticmethod
    def parse(raw: list, source: str = "") -> list[dict]:
        """`source` ya no forma parte del id: el mismo evento en las dos descargas (esta semana y la siguiente) se deduplica."""
        out = []
        for e in raw:
            if not isinstance(e, dict) or not e.get("title") or not e.get("date"):
                continue
            try:
                when = datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00"))
            except ValueError:
                continue
            impact = str(e.get("impact") or "").lower()
            impact = impact if impact in IMPACTS else "low"
            country = str(e.get("country") or "").upper() or "ALL"
            slug = "".join(ch for ch in str(e["title"]).lower() if ch.isalnum())[:40]
            out.append({"id": f"{when.strftime('%Y%m%d%H%M')}-{country}-{slug}", "time": when.isoformat(), "country": country,
                        "country_name": COUNTRY_NAMES.get(country, country), "title": str(e["title"]).strip(), "impact": impact,
                        "forecast": str(e.get("forecast") or "").strip() or None, "previous": str(e.get("previous") or "").strip() or None,
                        "actual": str(e.get("actual") or "").strip() or None})
        return out

    async def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._fetched_at is not None and now - self._fetched_at < self.ttl_s:
            return
        if not force and now < self._lock_until:
            return
        events: list[dict] = []
        errors: list[str] = []
        for k, url in enumerate(URLS):
            try:
                events += self.parse(await self.fetcher(url), f"w{k}-")
            except Exception as exc:
                errors.append(f"{url.rsplit('/', 1)[-1]}: {str(exc)[:120]}")
        if events:
            seen: set[str] = set()
            self._events = sorted([e for e in events if not (e["id"] in seen or seen.add(e["id"]))], key=lambda e: e["time"])
            self._fetched_at = now
            self._error = "; ".join(errors) or None
        else:
            self._error = "; ".join(errors) or "sin eventos en la fuente"
            self._lock_until = now + 120                # no insistir cada petición cuando la fuente falla
            logger.warning(f"Calendario económico: {self._error}")

    # ---- consulta ----
    async def get(self, days: int = 7, countries: list[str] | None = None, min_impact: str = "low") -> dict:
        await self.refresh()
        floor = IMPACTS.get(min_impact, 1)
        # desde hace una semana (para ver la semana en curso completa, con lo ya publicado) hasta `days` días adelante
        start = self.now().astimezone() - timedelta(days=7)
        end = self.now().astimezone() + timedelta(days=max(1, min(days, 21)) + 1)
        wanted = {c.strip().upper() for c in (countries or []) if c.strip()}
        rows = []
        for e in self._events:
            t = datetime.fromisoformat(e["time"])
            if t < start or t > end:
                continue
            if wanted and e["country"] not in wanted:
                continue
            if IMPACTS[e["impact"]] < floor and e["impact"] != "holiday":
                continue
            rows.append(e)
        return {"events": rows, "fetched_at": (time.time() - (time.monotonic() - self._fetched_at)) if self._fetched_at is not None else None,
                "error": self._error, "countries": sorted({e["country"] for e in self._events}), "source": "ForexFactory (faireconomy.media)"}

    # ---- demo ----
    def seed_demo(self) -> None:
        """Simulador: una semana de eventos de EE. UU. inventados para ver la pestaña sin conexión."""
        rng = random.Random(3)
        names = [("high", "Nóminas no agrícolas"), ("high", "IPC (interanual)"), ("high", "Decisión de tipos de la Fed"), ("medium", "PMI manufacturero"),
                 ("medium", "Peticiones de subsidio"), ("medium", "Ventas minoristas"), ("low", "Inventarios de crudo"), ("low", "Subasta de bonos a 10 años"),
                 ("medium", "Confianza del consumidor (Michigan)"), ("high", "PIB (trimestral)"), ("low", "Discurso de la Fed"), ("medium", "Pedidos de bienes duraderos")]
        base = self.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        monday = base - timedelta(days=base.weekday())
        out = []
        for d in range(0, 12):
            day = monday + timedelta(days=d)
            if day.weekday() >= 5:
                continue
            for k in range(rng.randint(2, 5)):
                imp, title = rng.choice(names)
                when = day.replace(hour=rng.choice([8, 8, 10, 10, 12, 14]), minute=rng.choice([0, 30]))
                out.append({"title": title, "country": "USD", "date": when.isoformat(), "impact": imp.capitalize(),
                            "forecast": f"{rng.uniform(-1, 4):.1f}%" if imp != "low" else "", "previous": f"{rng.uniform(-1, 4):.1f}%" if imp != "low" else ""})
        out.append({"title": "BCE: decisión de tipos", "country": "EUR", "date": (monday + timedelta(days=3, hours=8, minutes=15)).isoformat(), "impact": "High", "forecast": "3.65%", "previous": "3.65%"})
        self._events = sorted(self.parse(out, "demo-"), key=lambda e: e["time"])
        self._fetched_at = time.monotonic()
        self._error = None
