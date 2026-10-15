"""Análisis con IA (Claude) para el calendario económico.

Antes de cada noticia vigilada se pide a Claude un análisis del impacto posible; cuando sale el dato, otro con el resultado
y su efecto esperado en el mercado. Cada análisis se pide una sola vez por evento (se guarda en SQLite). Sin
ANTHROPIC_API_KEY en engine/.env el engine usa un análisis por reglas (`heuristic_pre` / `heuristic_post`) para que los
avisos sigan saliendo; la consola indica cuál de los dos es.
"""
import json
import re
from typing import Any

from loguru import logger

SYSTEM = (
    "Eres un analista macro que asesora a un trader intradía de futuros de EE. UU. (ES/NQ/YM/RTY, CL, GC y sus micros) que "
    "copia sus operaciones a cuentas de prop firm con límites de pérdida diaria y drawdown. Responde SIEMPRE en español, "
    "concreto y sin avisos legales. Devuelve ÚNICAMENTE un objeto JSON (sin texto alrededor) con estas claves:\n"
    '  "summary": 2-3 frases: qué mide el dato, qué espera el mercado y por qué importa hoy.\n'
    '  "bias": sesgo esperado para los índices: "alcista" | "bajista" | "neutral" | "volátil".\n'
    '  "move": movimiento típico esperado en los índices en los 15-30 min siguientes (p. ej. "0,3-0,8 %").\n'
    '  "advice": 1-2 frases accionables para proteger las cuentas: reducir tamaño, no abrir X min antes/después, ampliar stops, cerrar antes...\n'
    '  "confidence": número de 0 a 1.\n'
)
SYSTEM_POST = SYSTEM.replace('  "summary": 2-3 frases: qué mide el dato, qué espera el mercado y por qué importa hoy.\n',
                             '  "summary": 2-3 frases: qué ha salido frente a lo previsto y a lo anterior, y cómo lo leerá el mercado (tipos, Fed, riesgo).\n'
                             '  "surprise": "mayor" | "menor" | "en línea" respecto a la previsión.\n')

IMPACT_ES = {"high": "alto", "medium": "medio", "low": "bajo", "holiday": "festivo"}


def parse_json(text: str) -> dict:
    """Primer objeto JSON del texto; si no lo hay, el texto entero como resumen."""
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return d
        except ValueError:
            pass
    return {"summary": text.strip()}


def to_number(s: str | None) -> float | None:
    """'150K' → 150000, '3.2%' → 3.2, '-1.2M' → -1200000, '0.25%' → 0.25, '2.5' → 2.5. None si no es numérico."""
    if not s:
        return None
    t = str(s).strip().replace(",", "").replace("%", "")
    m = re.fullmatch(r"([<>]?)\s*(-?\d+(?:\.\d+)?)\s*([KMBT]?)", t, re.I)
    if not m:
        return None
    v = float(m.group(2))
    return v * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(3).upper()]


def surprise_of(event: dict) -> dict | None:
    """Sorpresa numérica dato real − previsión (y relativa a la previsión). None si falta alguno o no son números."""
    a, f = to_number(event.get("actual")), to_number(event.get("forecast"))
    if a is None or f is None:
        return None
    diff = a - f
    rel = diff / abs(f) if f else None
    tag = "en línea" if diff == 0 or (rel is not None and abs(rel) < 0.02) else "mayor" if diff > 0 else "menor"
    return {"actual": a, "forecast": f, "diff": diff, "rel": rel, "tag": tag}


def heuristic_pre(event: dict) -> dict:
    imp = event.get("impact", "low")
    move = {"high": "0,3-1 %", "medium": "0,1-0,4 %", "low": "< 0,15 %"}.get(imp, "mínimo")
    advice = {"high": "No abras entradas nuevas desde 5 min antes hasta 10-15 min después; si llevas posición, reduce o cierra antes del dato y amplía el stop si la mantienes.",
              "medium": "Evita entrar en los 2-3 min previos; espera a que el primer impulso se agote antes de operar en la dirección del movimiento.",
              "low": "Normalmente no mueve el mercado; sigue el plan y vigila solo si coincide con otros datos."}.get(imp, "Sin acción especial.")
    fc = f" Previsión {event['forecast']}" + (f" (anterior {event['previous']})." if event.get("previous") else ".") if event.get("forecast") else ""
    return {"summary": f"{event['title']} ({event.get('country_name') or event.get('country')}) es un dato de impacto {IMPACT_ES.get(imp, imp)}.{fc}",
            "bias": "volátil" if imp == "high" else "neutral", "move": move, "advice": advice, "confidence": 0.4}


def heuristic_post(event: dict, reaction: dict | None = None) -> dict:
    sp = surprise_of(event)
    if sp:
        summary = (f"{event['title']}: real {event['actual']} frente a una previsión de {event['forecast']}"
                   + (f" y un anterior de {event['previous']}" if event.get("previous") else "")
                   + f". Dato {sp['tag']} de lo esperado" + (f" ({sp['rel'] * 100:+.1f} %)" if sp["rel"] is not None else "") + ".")
    else:
        summary = f"{event['title']}: real {event.get('actual') or '—'}" + (f" (previsión {event['forecast']})" if event.get("forecast") else "") + "."
    bias, move = "neutral", "sin datos de precio"
    if reaction and reaction.get("px_before") and (reaction.get("px_15m") or reaction.get("px_5m")):
        px = reaction.get("px_15m") or reaction.get("px_5m")
        pct = (px - reaction["px_before"]) / reaction["px_before"] * 100
        move = f"{reaction['symbol']} {pct:+.2f} % tras el dato"
        bias = "alcista" if pct > 0.05 else "bajista" if pct < -0.05 else "neutral"
    return {"summary": summary, "surprise": sp["tag"] if sp else "desconocida", "bias": bias, "move": move,
            "advice": "Espera a que el mercado digiera el dato (10-15 min) antes de volver a copiar con tamaño normal.", "confidence": 0.35}


class AIService:
    def __init__(self, api_key: str = "", model: str = "claude-opus-5-5", max_tokens: int = 2000, timeout_s: float = 90.0) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self._client: Any = None
        self.last_error: str | None = None
        self.calls = 0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict:
        return {"enabled": self.enabled, "model": self.model, "last_error": self.last_error, "calls": self.calls}

    async def ask(self, system: str, prompt: str) -> dict:
        """Una llamada a Claude; devuelve el JSON parseado (+ "model"). Lanza RuntimeError con un mensaje legible si falla."""
        if not self.enabled:
            raise RuntimeError("sin ANTHROPIC_API_KEY en engine/.env")
        import anthropic
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self.api_key, max_retries=2)
        try:
            self.calls += 1
            r = await self._client.messages.create(model=self.model, max_tokens=self.max_tokens, system=system,
                                                   thinking={"type": "adaptive"}, messages=[{"role": "user", "content": prompt}],
                                                   timeout=self.timeout_s)
        except anthropic.AuthenticationError:
            self.last_error = "ANTHROPIC_API_KEY no válida"
            raise RuntimeError(self.last_error)
        except anthropic.RateLimitError:
            self.last_error = "límite de peticiones de la API de Claude; se reintentará más tarde"
            raise RuntimeError(self.last_error)
        except anthropic.APIStatusError as exc:
            self.last_error = f"API de Claude: {exc.status_code} {str(getattr(exc, 'message', exc))[:160]}"
            raise RuntimeError(self.last_error)
        except anthropic.APIConnectionError as exc:
            self.last_error = f"sin conexión con la API de Claude ({str(exc)[:80]})"
            raise RuntimeError(self.last_error)
        if r.stop_reason == "refusal":
            self.last_error = "la IA rechazó la petición"
            raise RuntimeError(self.last_error)
        text = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
        self.last_error = None
        return {**parse_json(text), "model": r.model}

    @staticmethod
    def _event_text(event: dict) -> str:
        return (f"Evento: {event['title']} · país: {event.get('country_name') or event.get('country')} · impacto: {IMPACT_ES.get(event.get('impact', ''), event.get('impact'))}\n"
                f"Hora: {event['time']}\nPrevisión: {event.get('forecast') or '—'} · Anterior: {event.get('previous') or '—'}")

    async def analyze_pre(self, event: dict, context: str = "") -> dict:
        prompt = (f"Falta poco para esta noticia. Analiza el impacto posible para quien opera futuros de índices de EE. UU. hoy.\n"
                  f"{self._event_text(event)}\n{context}").strip()
        return await self.ask(SYSTEM, prompt)

    async def analyze_post(self, event: dict, reaction: dict | None = None, context: str = "") -> dict:
        sp = surprise_of(event)
        rx = ""
        if reaction and reaction.get("px_before"):
            parts = [f"{k.replace('px_', '')}: {v}" for k, v in reaction.items() if k.startswith("px_") and v]
            rx = f"Reacción del precio ({reaction.get('symbol')}): antes {reaction['px_before']}; " + ", ".join(parts[1:]) + \
                 (f"; máximo {reaction['high']} / mínimo {reaction['low']}" if reaction.get("high") else "") + "\n"
        prompt = (f"Ya se ha publicado el dato. Analiza el resultado y cómo puede afectar al mercado en la sesión.\n{self._event_text(event)}\n"
                  f"Real: {event.get('actual') or '—'}" + (f" (sorpresa {sp['tag']}, {sp['diff']:+g})" if sp else "") + f"\n{rx}{context}").strip()
        return await self.ask(SYSTEM_POST, prompt)
