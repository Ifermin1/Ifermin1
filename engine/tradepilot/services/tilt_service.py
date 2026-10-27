"""Detector de tilt: cuando el trader empieza a operar por rabia, no por plan.

Se evalúa en cada operación cerrada (por cuenta, sobre el día):
- **racha**: N pérdidas seguidas (TILT_STREAK, 3 por defecto);
- **tamaño creciente tras perder**: la operación perdedora de ahora lleva más contratos que la perdedora anterior (martingala);
- **reentrada rápida**: abrir otra vez en menos de TILT_REENTRY_S (60 s) tras una pérdida, dos veces seguidas;
- **frecuencia**: más de TILT_MAX_PER_HOUR operaciones en la última hora;
- **hundimiento**: el neto del día cae por debajo de −TILT_DAY_DROP_USD cuando hace menos de 2 h iba en positivo.

Con dos señales o más (o racha + cualquiera) se audita TILT (llega al teléfono) con la propuesta de parar; con
`auto_pause_min` > 0 además se pausa la cuenta (límite `trading_halted`, motivo "tilt") y se reanuda sola pasados esos
minutos (TILT_RESUME). La pausa se puede levantar antes en Riesgo. Con clave de IA, la frase del aviso la escribe Claude
con las cifras; sin clave, texto por reglas.
"""
import asyncio
import json
import time
from datetime import datetime

from loguru import logger

from tradepilot.domain import tradestats as ts
from tradepilot.domain.risk import RiskLimit

TILT_SYSTEM = (
    "Eres el coach de un trader intradía. Te paso las señales de tilt detectadas en una cuenta hoy (racha, tamaño creciente, reentradas rápidas, "
    "frecuencia, hundimiento) con cifras. Escribe en español, en 2-3 frases directas y sin sermón, qué está pasando y qué hacer ahora mismo. "
    'Devuelve ÚNICAMENTE un JSON: {"message": "..."}'
)


class TiltService:
    def __init__(self, store, audit, risk, accounts, ai=None, streak: int = 3, reentry_s: int = 60, max_per_hour: int = 12, day_drop_usd: float = 500.0, auto_pause_min: int = 0) -> None:
        self.store, self.audit, self.risk, self.accounts, self.ai = store, audit, risk, accounts, ai
        cfg = json.loads(store.get_kv("tilt.config") or "{}") if store else {}
        self.config = {"enabled": bool(cfg.get("enabled", True)), "streak": int(cfg.get("streak", streak)), "reentry_s": int(cfg.get("reentry_s", reentry_s)),
                       "max_per_hour": int(cfg.get("max_per_hour", max_per_hour)), "day_drop_usd": float(cfg.get("day_drop_usd", day_drop_usd)), "auto_pause_min": int(cfg.get("auto_pause_min", auto_pause_min))}
        self.last_signals: dict[str, dict] = {}
        self.paused: dict[str, float] = {}             # cuenta -> hasta (epoch)
        self._announced: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self.now = datetime.now

    def set_config(self, **changes) -> dict:
        for k, v in changes.items():
            if k in self.config and v is not None:
                self.config[k] = type(self.config[k])(v)
        if self.store:
            self.store.set_kv("tilt.config", json.dumps(self.config))
        return self.config

    # ---- ciclo: reanudar las pausas que caducan ----
    async def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                self.expire_pauses()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Tilt: {exc}")

    def expire_pauses(self) -> None:
        now = time.time()
        for acc, until in list(self.paused.items()):
            if now >= until:
                self.resume(acc, "fin de la pausa por tilt")

    def resume(self, account: str, reason: str = "a mano") -> bool:
        self.paused.pop(account, None)
        lim = self.risk.limits.get(account)
        if lim is None or not lim.trading_halted or lim.halted_reason != "tilt":
            return False
        self.risk.upsert_limit(lim.model_copy(update={"trading_halted": False, "halted_reason": "", "halted_at": None}))
        self.audit.log("TILT_RESUME", f"{account}: pausa por tilt levantada ({reason})", target=account)
        return True

    # ---- evaluación ----
    def signals_for(self, account: str, trades: list[dict]) -> list[dict]:
        c = self.config
        trades = sorted(trades, key=lambda t: t.get("closed_at") or "")
        nets = [ts.net_of(t) for t in trades]
        out: list[dict] = []
        streak = 0
        for v in reversed(nets):
            if v < 0:
                streak += 1
            else:
                break
        if streak >= c["streak"]:
            out.append({"kind": "streak", "text": f"{streak} pérdidas seguidas ({sum(v for v in nets[-streak:]):+,.0f} $)", "weight": 2})
        losers = [t for t in trades if ts.net_of(t) < 0]
        if len(losers) >= 2 and int(losers[-1].get("quantity") or 0) > int(losers[-2].get("quantity") or 0) and ts.net_of(trades[-1]) < 0:
            out.append({"kind": "size_up", "text": f"tamaño creciente tras perder: {losers[-2].get('quantity')} → {losers[-1].get('quantity')} contratos", "weight": 2})
        fast = 0
        for prev, cur in zip(trades, trades[1:]):
            try:
                gap = (datetime.fromisoformat(cur["opened_at"]) - datetime.fromisoformat(prev["closed_at"])).total_seconds() if cur.get("opened_at") else None
            except (TypeError, ValueError):
                gap = None
            fast = fast + 1 if (gap is not None and gap < c["reentry_s"] and ts.net_of(prev) < 0) else 0
        if fast >= 2:
            out.append({"kind": "reentry", "text": f"{fast} reentradas en menos de {c['reentry_s']} s tras una pérdida", "weight": 1})
        if trades:
            try:
                last_t = datetime.fromisoformat(trades[-1]["closed_at"])
                hour = [t for t in trades if (last_t - datetime.fromisoformat(t["closed_at"])).total_seconds() <= 3600]
            except (TypeError, ValueError):
                hour = []
            if len(hour) > c["max_per_hour"]:
                out.append({"kind": "frequency", "text": f"{len(hour)} operaciones en la última hora", "weight": 1})
        cum, peak = 0.0, 0.0
        for v in nets:
            cum += v
            peak = max(peak, cum)
        if peak > 0 and cum <= -c["day_drop_usd"]:
            out.append({"kind": "day_drop", "text": f"el día iba {peak:+,.0f} $ y ahora va {cum:+,.0f} $", "weight": 1})
        return out

    async def on_trade(self, trade: dict) -> dict | None:
        """La llama el servicio de rendimiento al cerrar cada operación."""
        if not self.config["enabled"] or not self.store:
            return None
        acc = str(trade.get("account_id") or "")
        day = str(trade.get("day") or self.now().strftime("%Y-%m-%d"))
        trades = self.store.get_trades(day, day, acc)
        sig = self.signals_for(acc, trades)
        score = sum(s["weight"] for s in sig)
        self.last_signals[acc] = {"at": self.now().astimezone().isoformat(timespec="seconds"), "signals": sig, "score": score, "day": day}
        if score < 2:
            return None
        now = time.monotonic()
        if now - self._announced.get(acc, -1e9) < 900:
            return None
        self._announced[acc] = now
        msg = f"{acc}: " + "; ".join(s["text"] for s in sig) + ". Para 15 minutos, baja a la mitad de tamaño y vuelve solo con el plan."
        if self.ai is not None and self.ai.enabled:
            try:
                r = await self.ai.ask(TILT_SYSTEM, json.dumps({"account": acc, "signals": sig, "day_net": round(sum(ts.net_of(t) for t in trades), 2), "n_trades": len(trades)}, ensure_ascii=False), max_tokens=250)
                if r.get("message"):
                    msg = f"{acc}: {str(r['message']).strip()}"
            except Exception as exc:
                logger.warning(f"Tilt: la IA falló, texto por reglas: {exc}")
        paused = False
        if self.config["auto_pause_min"] > 0:
            paused = self.pause(acc, self.config["auto_pause_min"])
        self.audit.log("TILT", msg + (f" ⏸ Cuenta pausada {self.config['auto_pause_min']} min." if paused else ""), target=acc, details={"signals": sig, "score": score, "paused": paused})
        return self.last_signals[acc]

    def pause(self, account: str, minutes: int) -> bool:
        lim = self.risk.limits.get(account) or RiskLimit(account_id=account)
        if lim.trading_halted and lim.halted_reason != "tilt":
            return False
        self.risk.upsert_limit(lim.model_copy(update={"trading_halted": True, "halted_reason": "tilt", "halted_at": self.now()}))
        self.paused[account] = time.time() + minutes * 60
        return True

    def state(self) -> dict:
        return {"config": self.config, "ai": bool(self.ai is not None and self.ai.enabled),
                "accounts": {acc: {**v} for acc, v in self.last_signals.items()},
                "paused": {acc: datetime.fromtimestamp(until).astimezone().isoformat(timespec="seconds") for acc, until in self.paused.items()}}
