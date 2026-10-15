"""Calendario económico: eventos de la semana (y la siguiente) con su impacto, previsión y dato real; vigilancia continua;
análisis con IA antes y después de cada dato; reacción del precio; y métricas estadísticas.

Fuente: el JSON público de ForexFactory (nfs.faireconomy.media/ff_calendar_thisweek.json y _nextweek.json), sin clave.
Se vuelve a descargar cada NEWS_REFRESH_SECONDS (5 min): así aparecen los datos reales (`actual`) pocos minutos después de
publicarse. Cada descarga se cachea por separado: si la de la semana siguiente falla (ForexFactory a veces no la publica
y devuelve 404) se usa la última buena y no se muestra error; solo cuenta como fallo la de la semana en curso.

Vigilancia (`tick`, cada minuto): para los eventos vigilados (NEWS_COUNTRIES / NEWS_MIN_IMPACT) se emite NEWS_SOON cuando
faltan menos de NEWS_ALERT_MINUTES (Inicio + Telegram) con el análisis previo, y NEWS_RESULT cuando aparece el dato real,
con el análisis del resultado y la reacción del precio medida con los ticks del addon (5, 15 y 30 min después). Los
eventos, análisis y reacciones se guardan en SQLite para las métricas (`stats`).
"""
import asyncio
import json
import random
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from loguru import logger

from tradepilot.core.events import TOPIC_PRICE, EventBus
from tradepilot.services.ai_service import AIService, IMPACT_ES, heuristic_post, heuristic_pre, surprise_of, to_number

URLS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json", "https://nfs.faireconomy.media/ff_calendar_nextweek.json")
COUNTRY_NAMES = {"USD": "EE. UU.", "EUR": "Eurozona", "GBP": "Reino Unido", "JPY": "Japón", "CAD": "Canadá", "AUD": "Australia",
                 "NZD": "N. Zelanda", "CHF": "Suiza", "CNY": "China", "ALL": "Global"}
IMPACTS = {"high": 3, "medium": 2, "low": 1, "holiday": 0}
WINDOW_MIN = 15            # ± minutos alrededor de una noticia para considerar que una operación "estaba en la noticia"
RESULT_WINDOW_MIN = 90     # un dato publicado hace más de esto ya no se analiza ni se avisa (p. ej. al arrancar el engine)
ALERTED_KEY = "news.alerted"


def _root(symbol: str) -> str:
    return symbol.split(" ")[0].upper()


def _hm(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%H:%M")


class NewsService:
    def __init__(self, fetcher: Callable[[str], Awaitable[list]] | None = None, ttl_s: float = 300.0, store=None, ai: AIService | None = None,
                 audit=None, bus: EventBus | None = None, alert_minutes: int = 30, countries: str = "USD", min_impact: str = "low") -> None:
        self.fetcher = fetcher or self._http
        self.ttl_s = ttl_s
        self.store = store
        self.ai = ai or AIService()
        self.audit = audit
        self.bus = bus
        self.alert_minutes = max(1, int(alert_minutes))
        self.countries = {c.strip().upper() for c in countries.split(",") if c.strip()}
        self.min_impact = min_impact if min_impact in IMPACTS else "low"
        self.now = datetime.now
        self.loop_s = 60.0
        self.demo = False
        self._events: list[dict] = []
        self._feeds: dict[str, list[dict]] = {}
        self._fetched_at: float | None = None
        self._error: str | None = None
        self._lock_until = 0.0
        self._ticks: dict[str, deque] = {}
        self._analyses: dict[tuple[str, str], dict] = {}
        self._ai_attempts: dict[tuple[str, str], int] = {}
        self._inflight: dict[tuple[str, str], asyncio.Task] = {}
        self._reactions: dict[tuple[str, str], dict] = {}
        self._alerted: list[str] = []
        self._task: asyncio.Task | None = None
        if store is not None:
            for a in store.get_news_analyses():
                self._analyses[(a["event_id"], a["kind"])] = {"source": a["source"], "model": a["model"], "data": a["data"], "created_at": a["created_at"]}
            try:
                self._alerted = json.loads(store.get_kv(ALERTED_KEY) or "[]")
            except ValueError:
                self._alerted = []
        if bus is not None:
            bus.subscribe(TOPIC_PRICE, self._on_price)

    # ---- ciclo de vida ----
    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        for t in self._inflight.values():
            t.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(f"Vigilancia del calendario: {exc}")
            await asyncio.sleep(self.loop_s)

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
        if self.demo:
            return
        now = time.monotonic()
        if not force and self._fetched_at is not None and now - self._fetched_at < self.ttl_s:
            return
        if not force and now < self._lock_until:
            return
        errors: list[str] = []
        for k, url in enumerate(URLS):
            name = url.rsplit("/", 1)[-1]
            try:
                self._feeds[url] = self.parse(await self.fetcher(url), f"w{k}-")
            except Exception as exc:
                if k == 0:
                    errors.append(f"{name}: {str(exc)[:120]}")
                else:
                    logger.debug(f"Calendario: {name} no disponible ({str(exc)[:80]}); se mantiene lo último descargado")
        seen: set[str] = set()
        events = [e for url in URLS for e in self._feeds.get(url, []) if not (e["id"] in seen or seen.add(e["id"]))]
        if events:
            self._events = sorted(events, key=lambda e: e["time"])
            self._fetched_at = now
            self._error = "; ".join(errors) or None
            if self.store is not None:
                try:
                    self.store.upsert_news_events(self._events, self.now().astimezone().isoformat(timespec="seconds"))
                except Exception as exc:
                    logger.warning(f"No se pudo guardar el calendario: {exc}")
            if errors:
                self._lock_until = now + 120
                logger.warning(f"Calendario económico: {self._error} (se muestra lo último descargado)")
        else:
            self._error = "; ".join(errors) or "sin eventos en la fuente"
            self._lock_until = now + 120                # no insistir cada petición cuando la fuente falla
            logger.warning(f"Calendario económico: {self._error}")

    # ---- consulta ----
    def _watched(self, e: dict) -> bool:
        return e["impact"] != "holiday" and IMPACTS[e["impact"]] >= IMPACTS[self.min_impact] and (not self.countries or e["country"] in self.countries)

    def _analysis_of(self, event_id: str) -> dict:
        return {k: self._analyses[(event_id, k)] for k in ("pre", "post") if (event_id, k) in self._analyses}

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
            rows.append({**e, "watched": self._watched(e)})
        ids = [e["id"] for e in rows]
        return {"events": rows, "fetched_at": (time.time() - (time.monotonic() - self._fetched_at)) if self._fetched_at is not None else None,
                "error": self._error, "countries": sorted({e["country"] for e in self._events}), "source": "ForexFactory (faireconomy.media)",
                "refresh_s": int(self.ttl_s), "analyses": {i: a for i in ids if (a := self._analysis_of(i))},
                "reactions": {i: r for i in ids if (r := self.reactions_of(i))}, "ai": self.ai.status(),
                "watch": {"countries": sorted(self.countries), "min_impact": self.min_impact, "alert_minutes": self.alert_minutes}}

    def alerts(self) -> dict:
        """Para Inicio: noticias vigiladas a punto de salir (con su análisis previo), la siguiente, y los últimos resultados."""
        now = self.now().astimezone()
        upcoming, results, nxt = [], [], None
        for e in self._events:
            if not self._watched(e):
                continue
            t = datetime.fromisoformat(e["time"])
            mins = (t - now).total_seconds() / 60
            if 0 <= mins <= self.alert_minutes:
                upcoming.append({"event": e, "minutes": round(mins), "analysis": self._analyses.get((e["id"], "pre"))})
            elif mins > self.alert_minutes and nxt is None:
                nxt = {"event": e, "minutes": round(mins)}
            elif -RESULT_WINDOW_MIN <= mins < 0 and e.get("actual"):
                if self._ticks:
                    self._update_reactions(e)
                results.append({"event": e, "minutes": round(mins), "analysis": self._analyses.get((e["id"], "post")),
                                "reaction": self.best_reaction(e["id"]), "surprise": surprise_of(e)})
        results.sort(key=lambda r: r["event"]["time"], reverse=True)
        return {"upcoming": upcoming, "next": nxt, "results": results[:3], "ai": self.ai.status(), "window_min": self.alert_minutes,
                "countries": sorted(self.countries), "min_impact": self.min_impact}

    # ---- precio: ticks del addon y reacción tras cada dato ----
    async def _on_price(self, data: dict) -> None:
        try:
            self.on_tick(str(data.get("symbol") or ""), float(data.get("last") or 0))
        except (TypeError, ValueError):
            pass

    def on_tick(self, symbol: str, last: float, ts: float | None = None) -> None:
        if not symbol or last <= 0:
            return
        q = self._ticks.setdefault(_root(symbol), deque(maxlen=40000))
        q.append((ts if ts is not None else self.now().timestamp(), last))
        cutoff = (ts if ts is not None else self.now().timestamp()) - 3 * 3600
        while q and q[0][0] < cutoff:
            q.popleft()

    def reaction(self, event: dict, symbol: str) -> dict | None:
        """Precio justo antes del dato y 5/15/30 min después (primer tick a partir de ese momento), máximo y mínimo en 30 min."""
        q = self._ticks.get(_root(symbol))
        if not q:
            return None
        t0 = datetime.fromisoformat(event["time"]).timestamp()
        before = [px for ts, px in q if t0 - 1800 <= ts <= t0]
        if not before:
            return None
        out = {"symbol": _root(symbol), "px_before": before[-1], "px_5m": None, "px_15m": None, "px_30m": None, "high": None, "low": None}
        after = [(ts, px) for ts, px in q if t0 < ts <= t0 + 1800]
        for key, mins in (("px_5m", 5), ("px_15m", 15), ("px_30m", 30)):
            cand = [px for ts, px in after if t0 + mins * 60 <= ts <= t0 + mins * 60 + 180]
            out[key] = cand[0] if cand else None
        if after:
            out["high"], out["low"] = max(px for _, px in after), min(px for _, px in after)
        return out

    def _update_reactions(self, event: dict) -> None:
        for sym in list(self._ticks):
            r = self.reaction(event, sym)
            if r is None:
                continue
            key = (event["id"], r["symbol"])
            if self._reactions.get(key) != r:
                self._reactions[key] = r
                if self.store is not None:
                    self.store.upsert_news_reaction(event["id"], r["symbol"], r, self.now().astimezone().isoformat(timespec="seconds"))

    def reactions_of(self, event_id: str) -> list[dict]:
        return [r for (eid, _), r in self._reactions.items() if eid == event_id]

    def best_reaction(self, event_id: str) -> dict | None:
        rs = self.reactions_of(event_id)
        if not rs:
            return None
        return max(rs, key=lambda r: ((r.get("px_15m") or r.get("px_5m") or 0) > 0, r["symbol"] in ("ES", "MES", "NQ", "MNQ")))

    # ---- análisis (IA o reglas) ----
    async def ensure_analysis(self, event: dict, kind: str, force: bool = False) -> dict:
        """Devuelve el análisis guardado o lo genera: con Claude si hay clave (y se reintenta hasta 3 veces si falló y quedó
        el de reglas), si no con las reglas de `ai_service`."""
        key = (event["id"], kind)
        cur = self._analyses.get(key)
        if cur and not force and (cur["source"] == "ia" or not self.ai.enabled or self._ai_attempts.get(key, 0) >= 3):
            return cur
        if key in self._inflight and not force:
            return await self._inflight[key]
        task = asyncio.ensure_future(self._analyze(event, kind))
        self._inflight[key] = task
        try:
            return await task
        finally:
            self._inflight.pop(key, None)

    async def _analyze(self, event: dict, kind: str) -> dict:
        key = (event["id"], kind)
        reaction = self.best_reaction(event["id"]) if kind == "post" else None
        data, source, model = None, "regla", ""
        if self.ai.enabled:
            self._ai_attempts[key] = self._ai_attempts.get(key, 0) + 1
            try:
                data = await (self.ai.analyze_pre(event) if kind == "pre" else self.ai.analyze_post(event, reaction))
                source, model = "ia", str(data.pop("model", self.ai.model))
            except Exception as exc:
                logger.warning(f"Análisis IA de '{event['title']}' ({kind}): {exc}")
        if data is None:
            data = heuristic_pre(event) if kind == "pre" else heuristic_post(event, reaction)
        out = {"source": source, "model": model, "data": data, "created_at": self.now().astimezone().isoformat(timespec="seconds")}
        self._analyses[key] = out
        if self.store is not None:
            try:
                self.store.save_news_analysis(event["id"], kind, source, model, data, out["created_at"])
            except Exception as exc:
                logger.warning(f"No se pudo guardar el análisis: {exc}")
        return out

    def find(self, event_id: str) -> dict | None:
        return next((e for e in self._events if e["id"] == event_id), None)

    # ---- vigilancia ----
    async def tick(self) -> None:
        await self.refresh()
        now = self.now().astimezone()
        for e in self._events:
            if not self._watched(e):
                continue
            t = datetime.fromisoformat(e["time"])
            mins = (t - now).total_seconds() / 60
            if -35 <= mins <= 0 and self._ticks:
                self._update_reactions(e)
            if 0 <= mins <= self.alert_minutes + 10 and (e["id"], "pre") not in self._analyses and (e["id"], "pre") not in self._inflight:
                asyncio.ensure_future(self.ensure_analysis(e, "pre"))      # se prepara antes de que entre en la ventana de aviso
            if 0 <= mins <= self.alert_minutes and e["id"] not in self._alerted:
                self._alerted = (self._alerted + [e["id"]])[-300:]
                if self.store is not None:
                    self.store.set_kv(ALERTED_KEY, json.dumps(self._alerted))
                a = await self.ensure_analysis(e, "pre")
                self._log("NEWS_SOON", e, f"{e['title']} ({e['country']}) a las {_hm(e['time'])} · en {round(mins)} min · impacto {IMPACT_ES.get(e['impact'], e['impact'])}"
                          + (f" · previsión {e['forecast']}" if e.get("forecast") else "") + (f" · anterior {e['previous']}" if e.get("previous") else "")
                          + f"\n{self._text(a)}", a, mins)
            if -RESULT_WINDOW_MIN <= mins < 0 and e.get("actual") and (e["id"], "post") not in self._analyses and (e["id"], "post") not in self._inflight:
                # con ticks del addon se espera a tener el precio 5 min después para que el análisis incluya la reacción
                if self._ticks and -5 < mins and self.best_reaction(e["id"]) is not None and (self.best_reaction(e["id"]) or {}).get("px_5m") is None:
                    continue
                a = await self.ensure_analysis(e, "post")
                sp = surprise_of(e)
                self._log("NEWS_RESULT", e, f"{e['title']} ({e['country']}): real {e['actual']}" + (f" · previsión {e['forecast']}" if e.get("forecast") else "")
                          + (f" · anterior {e['previous']}" if e.get("previous") else "") + (f" · dato {sp['tag']}" if sp else "") + f"\n{self._text(a)}", a, mins)

    @staticmethod
    def _text(a: dict) -> str:
        d = a.get("data", {})
        bits = [d.get("summary", "")]
        if d.get("move"):
            bits.append(f"Movimiento esperado: {d['move']}.")
        if d.get("advice"):
            bits.append(d["advice"])
        if a.get("source") != "ia":
            bits.append("(análisis por reglas: pon ANTHROPIC_API_KEY en engine/.env para usar la IA)")
        return " ".join(b.strip() for b in bits if b).strip()

    def _log(self, etype: str, e: dict, message: str, analysis: dict, mins: float) -> None:
        if self.audit is None:
            return
        self.audit.log(etype, message, details={"event_id": e["id"], "title": e["title"], "impact": e["impact"], "time": e["time"], "minutes": round(mins),
                                                "source": analysis.get("source"), "analysis": analysis.get("data"), "reaction": self.best_reaction(e["id"])})

    # ---- métricas ----
    def stats(self, days: int = 90) -> dict:
        """Operaciones dentro de ±15 min de una noticia de impacto medio/alto frente al resto, reacción media del precio por
        impacto y por dato, y sorpresas (real frente a previsión)."""
        days = max(7, min(int(days), 365))
        now = self.now().astimezone()
        start = now - timedelta(days=days)
        events = [e for e in (self.store.get_news_events(start.isoformat(), now.isoformat()) if self.store else [])
                  if e["impact"] in ("medium", "high") and (not self.countries or e["country"] in self.countries)]
        ev_t = [(datetime.fromisoformat(e["time"]).timestamp(), e) for e in events]
        trades = self.store.get_trades(start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")) if self.store else []

        def bucket() -> dict:
            return {"n": 0, "net": 0.0, "wins": 0, "gross_profit": 0.0, "gross_loss": 0.0}

        def add(b: dict, net: float) -> None:
            b["n"] += 1
            b["net"] += net
            b["wins"] += 1 if net > 0 else 0
            if net > 0:
                b["gross_profit"] += net
            else:
                b["gross_loss"] -= net

        def done(b: dict) -> dict:
            return {**{k: round(v, 2) if isinstance(v, float) else v for k, v in b.items()},
                    "win_rate": round(b["wins"] / b["n"], 4) if b["n"] else None, "avg": round(b["net"] / b["n"], 2) if b["n"] else None,
                    "pf": round(b["gross_profit"] / b["gross_loss"], 2) if b["gross_loss"] > 0 else None}

        buckets = {"near_high": bucket(), "near_medium": bucket(), "outside": bucket()}
        by_title: dict[str, dict] = {}
        w = WINDOW_MIN * 60
        for t in trades:
            try:
                closed = datetime.fromisoformat(t["closed_at"]).timestamp()
                opened = datetime.fromisoformat(t["opened_at"]).timestamp() if t.get("opened_at") else closed
            except (TypeError, ValueError):
                continue
            net = float(t.get("pnl") or 0) - float(t.get("commissions") or 0)
            hits = [e for et, e in ev_t if opened <= et + w and closed >= et - w]
            if not hits:
                add(buckets["outside"], net)
                continue
            top = max(hits, key=lambda e: IMPACTS[e["impact"]])
            add(buckets["near_high" if top["impact"] == "high" else "near_medium"], net)
            add(by_title.setdefault(top["title"], bucket()), net)
        titles = sorted(({"title": k, **done(v)} for k, v in by_title.items() if v["n"] >= 2), key=lambda x: x["n"], reverse=True)[:8]

        reactions = self.store.get_news_reactions(start.isoformat(), now.isoformat()) if self.store else []
        by_imp: dict[str, dict] = {}
        react_title: dict[str, list[float]] = {}
        agree = disagree = 0
        for r in reactions:
            if not r.get("px_before") or (self.countries and r["country"] not in self.countries):
                continue
            b = by_imp.setdefault(r["impact"], {"n": 0, "m5": [], "m15": [], "m30": [], "range": []})
            b["n"] += 1
            for key, lst in (("px_5m", "m5"), ("px_15m", "m15"), ("px_30m", "m30")):
                if r.get(key):
                    b[lst].append(abs(r[key] - r["px_before"]) / r["px_before"] * 100)
            if r.get("high") and r.get("low"):
                b["range"].append((r["high"] - r["low"]) / r["px_before"] * 100)
            if r.get("px_15m"):
                react_title.setdefault(r["title"], []).append(abs(r["px_15m"] - r["px_before"]) / r["px_before"] * 100)
                sp = surprise_of({"actual": r.get("actual"), "forecast": r.get("forecast")})
                if sp and sp["tag"] != "en línea":
                    if (r["px_15m"] - r["px_before"] > 0) == (sp["diff"] > 0):
                        agree += 1
                    else:
                        disagree += 1
        avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
        reaction_stats = {k: {"n": v["n"], "avg_5m": avg(v["m5"]), "avg_15m": avg(v["m15"]), "avg_30m": avg(v["m30"]), "avg_range": avg(v["range"]),
                              "max_15m": round(max(v["m15"]), 3) if v["m15"] else None} for k, v in by_imp.items()}
        movers = sorted(({"title": k, "n": len(v), "avg_15m": avg(v)} for k, v in react_title.items() if len(v) >= 2), key=lambda x: x["avg_15m"] or 0, reverse=True)[:8]

        sp_tot = {"n": 0, "mayor": 0, "menor": 0, "en línea": 0}
        sp_title: dict[str, dict] = {}
        for e in events:
            sp = surprise_of(e)
            if not sp:
                continue
            sp_tot["n"] += 1
            sp_tot[sp["tag"]] += 1
            st = sp_title.setdefault(e["title"], {"n": 0, "mayor": 0, "menor": 0, "en línea": 0, "rel": []})
            st["n"] += 1
            st[sp["tag"]] += 1
            if sp["rel"] is not None:
                st["rel"].append(abs(sp["rel"]) * 100)
        surprises = sorted(({"title": k, "n": v["n"], "beat": v["mayor"], "miss": v["menor"], "inline": v["en línea"], "avg_abs_rel": avg(v["rel"])}
                            for k, v in sp_title.items() if v["n"] >= 2), key=lambda x: x["n"], reverse=True)[:8]
        return {"days": days, "window_min": WINDOW_MIN, "n_events": len(events), "n_trades": len(trades),
                "trades": {k: done(v) for k, v in buckets.items()}, "by_title": titles,
                "reactions": reaction_stats, "movers": movers, "follows_surprise": {"agree": agree, "disagree": disagree},
                "surprises": {"total": sp_tot, "by_title": surprises}}

    # ---- demo ----
    def seed_demo(self) -> None:
        """Simulador: dos semanas de eventos de EE. UU. inventados, una noticia a punto de salir, otra recién publicada con su
        dato y ticks de ES alrededor (para ver el aviso, el resultado y la reacción), y un histórico guardado para las métricas."""
        self.demo = True
        rng = random.Random(3)
        names = [("high", "Nóminas no agrícolas"), ("high", "IPC (interanual)"), ("high", "Decisión de tipos de la Fed"), ("medium", "PMI manufacturero"),
                 ("medium", "Peticiones de subsidio"), ("medium", "Ventas minoristas"), ("low", "Inventarios de crudo"), ("low", "Subasta de bonos a 10 años"),
                 ("medium", "Confianza del consumidor (Michigan)"), ("high", "PIB (trimestral)"), ("low", "Discurso de la Fed"), ("medium", "Pedidos de bienes duraderos")]
        now = self.now().astimezone()
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        monday = base - timedelta(days=base.weekday())
        out = []
        for d in range(0, 12):
            day = monday + timedelta(days=d)
            if day.weekday() >= 5:
                continue
            for k in range(rng.randint(2, 5)):
                imp, title = rng.choice(names)
                when = day.replace(hour=rng.choice([8, 8, 10, 10, 12, 14]), minute=rng.choice([0, 30]))
                fc = rng.uniform(-1, 4)
                out.append({"title": title, "country": "USD", "date": when.isoformat(), "impact": imp.capitalize(),
                            "forecast": f"{fc:.1f}%" if imp != "low" else "", "previous": f"{rng.uniform(-1, 4):.1f}%" if imp != "low" else "",
                            "actual": f"{fc + rng.uniform(-0.6, 0.6):.1f}%" if imp != "low" and when < now - timedelta(minutes=100) else ""})
        # que los inventados no se mezclen con los tres diseñados para la demo (uno a punto de salir, otro recién publicado)
        out = [e for e in out if not (-100 <= (datetime.fromisoformat(e["date"]) - now).total_seconds() / 60 <= self.alert_minutes + 15)]
        out.append({"title": "BCE: decisión de tipos", "country": "EUR", "date": (monday + timedelta(days=3, hours=8, minutes=15)).isoformat(), "impact": "High", "forecast": "3.65%", "previous": "3.65%"})
        soon = (now + timedelta(minutes=12)).replace(second=0, microsecond=0)
        done = (now - timedelta(minutes=25)).replace(second=0, microsecond=0)
        out.append({"title": "Nóminas no agrícolas", "country": "USD", "date": soon.isoformat(), "impact": "High", "forecast": "150K", "previous": "142K"})
        out.append({"title": "Ventas minoristas (mensual)", "country": "USD", "date": (now + timedelta(minutes=48)).replace(second=0, microsecond=0).isoformat(), "impact": "Medium", "forecast": "0.3%", "previous": "0.6%"})
        out.append({"title": "IPC (interanual)", "country": "USD", "date": done.isoformat(), "impact": "High", "forecast": "3.1%", "previous": "3.0%", "actual": "3.4%"})
        self._events = sorted(self.parse(out, "demo-"), key=lambda e: e["time"])
        self._fetched_at = time.monotonic()
        self._error = None
        # ticks de ES alrededor del dato publicado: tranquilo antes, salto bajista al salir, recuperación parcial
        t0 = done.timestamp()
        px = 6000.0
        for s in range(-1800, int(now.timestamp() - t0) + 1, 15):
            px = px + rng.gauss(0, 0.6) + (-18.0 if 0 <= s < 60 else 0.0) + (0.25 if 120 < s < 900 else 0.0)
            self.on_tick("ES 12-26", round(px * 4) / 4, t0 + s)
        if self.store is not None and str(getattr(self.store, "db_path", ":memory:")) != ":memory:":
            self._seed_history(rng, now)

    def _seed_history(self, rng: random.Random, now: datetime) -> None:
        """60 días de datos publicados con su reacción (solo si aún no hay histórico): para que Métricas muestre algo."""
        if self.store.get_news_events((now - timedelta(days=60)).isoformat(), (now - timedelta(days=14)).isoformat()):
            return
        names = [("high", "Nóminas no agrícolas", "K", 150), ("high", "IPC (interanual)", "%", 3.1), ("medium", "Peticiones de subsidio", "K", 220),
                 ("medium", "Ventas minoristas", "%", 0.3), ("medium", "PMI manufacturero", "", 49.5), ("high", "PIB (trimestral)", "%", 2.4)]
        rows, reacts = [], []
        for d in range(60, 13, -1):
            day = (now - timedelta(days=d)).replace(hour=8, minute=30, second=0, microsecond=0)
            if day.weekday() >= 5:
                continue
            for imp, title, unit, fc in rng.sample(names, 2):
                a = fc * (1 + rng.uniform(-0.08, 0.08)) if unit != "%" else fc + rng.uniform(-0.4, 0.4)
                fmt = (lambda v: f"{v:.0f}K") if unit == "K" else (lambda v: f"{v:.1f}%") if unit == "%" else (lambda v: f"{v:.1f}")
                e = {"id": f"{day.strftime('%Y%m%d%H%M')}-USD-{''.join(ch for ch in title.lower() if ch.isalnum())[:40]}", "time": day.isoformat(), "country": "USD",
                     "title": title, "impact": imp, "forecast": fmt(fc), "previous": fmt(fc * (1 + rng.uniform(-0.05, 0.05))), "actual": fmt(a)}
                rows.append(e)
                px = 5900 + rng.uniform(-100, 100)
                amp = {"high": 0.55, "medium": 0.22}[imp] * px / 100
                m15 = rng.gauss(0, amp) * (1 if (a - fc) * rng.choice([1, 1, -1]) > 0 else -1)
                reacts.append((e["id"], {"symbol": "ES", "px_before": round(px, 2), "px_5m": round(px + m15 * 0.6, 2), "px_15m": round(px + m15, 2),
                                         "px_30m": round(px + m15 * 0.8, 2), "high": round(px + abs(m15) * 1.2, 2), "low": round(px - abs(m15) * 0.5, 2)}))
        ts = now.isoformat(timespec="seconds")
        self.store.upsert_news_events(rows, ts)
        for eid, r in reacts:
            self.store.upsert_news_reaction(eid, "ES", r, ts)
