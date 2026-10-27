"""Órdenes desde Telegram: el mismo bot que avisa también obedece, solo al chat configurado.

/estado · /posiciones · /pausar · /reanudar · /breakeven · /cerrar (pide confirmación con botones) · /ayuda.
Todo lo que hace queda en la auditoría con "desde Telegram". Cualquier otro chat se ignora.
"""
import secrets
import time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from tradepilot.services.notify_service import NotifyService

HELP = ("<b>TradePilot X</b> · órdenes por Telegram\n"
        "/estado — P&amp;L de hoy, cuentas, copia y kill switch\n"
        "/posiciones — posiciones abiertas con su flotante\n"
        "/breakeven — stop al precio de entrada en todas las posiciones\n"
        "/pausar — kill switch: no se copia ni se opera\n"
        "/reanudar — quita el kill switch\n"
        "/cerrar — CERRAR TODO (pide confirmación)\n"
        "Y en lenguaje normal: «pon los stops a breakeven», «cierra la mitad de NQ en Sim101», «¿cuánto llevo hoy?» (lo que mueva dinero pide confirmación)\n"
        "/ayuda — esta lista")
CONFIRM_TTL_S = 90


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _usd(v: float) -> str:
    return f"{v:+,.0f} $"


class TelegramCommands:
    def __init__(self, notify: "NotifyService", accounts, risk, trade=None, market=None, bridge=None) -> None:
        self.notify, self.accounts, self.risk, self.trade, self.market, self.bridge = notify, accounts, risk, trade, market, bridge
        self.pending: dict[str, tuple[str, float]] = {}      # nonce -> (acción, caduca)
        self.handled = 0
        self.assistant = None                                # AssistantService (lo fija el contenedor)

    # ---- entrada ----
    async def handle(self, update: dict) -> None:
        try:
            if "callback_query" in update:
                await self._callback(update["callback_query"])
            elif "message" in update:
                await self._message(update["message"])
        except Exception as exc:
            logger.exception(f"Telegram: error atendiendo la orden: {exc}")
            await self.notify.send(f"❗ No pude hacerlo: {_esc(exc)}")

    def _is_our_chat(self, chat_id) -> bool:
        return str(chat_id) == str(self.notify.config.chat_id)

    async def _message(self, m: dict) -> None:
        if not self._is_our_chat((m.get("chat") or {}).get("id")):
            logger.warning(f"Telegram: mensaje de un chat no autorizado ({(m.get('chat') or {}).get('id')}), ignorado")
            return
        text = str(m.get("text") or "").strip()
        if not text:
            return
        if not text.startswith("/") or text.lower().startswith("/ia "):
            await self._assist(text[4:] if text.lower().startswith("/ia ") else text)
            return
        cmd = text.split()[0].lower().split("@")[0]
        self.handled += 1
        if cmd in ("/start", "/ayuda", "/help"):
            await self.notify.send(HELP)
        elif cmd == "/estado":
            await self.notify.send(self.status_text())
        elif cmd == "/posiciones":
            await self.notify.send(self.positions_text())
        elif cmd == "/pausar":
            self.risk.set_kill_switch(True, "desde Telegram")
            await self.notify.send("🛑 Kill switch <b>activado</b> desde Telegram: no se copia ni se opera. /reanudar lo quita.")
        elif cmd == "/reanudar":
            self.risk.set_kill_switch(False)
            await self.notify.send("▶️ Kill switch <b>desactivado</b>: la copia sigue.")
        elif cmd == "/breakeven":
            await self.notify.send(await self.breakeven_all())
        elif cmd == "/cerrar":
            await self._ask_confirm("flatten", "⚠️ <b>¿CERRAR TODO?</b>\nSe cancelan las órdenes y se cierran a mercado todas las posiciones de todas las cuentas activas.\n" + self.positions_text(short=True))
        else:
            await self.notify.send("No conozco esa orden. " + HELP)

    async def _assist(self, text: str) -> None:
        """Frase en lenguaje normal: el asistente responde; si propone acciones, botón para confirmarlas."""
        if self.assistant is None:
            await self.notify.send("El asistente no está disponible en este engine.")
            return
        self.handled += 1
        try:
            r = await self.assistant.handle(text, "telegram")
        except Exception as exc:
            await self.notify.send(f"No pude interpretarlo: {_esc(exc)}")
            return
        plan = r.get("plan")
        if not plan:
            await self.notify.send(_esc(r["reply"]))
            return
        self.pending = {k: v for k, v in self.pending.items() if v[1] > time.time()}
        self.pending[plan["id"]] = ("assist", time.time() + CONFIRM_TTL_S)
        kb = {"inline_keyboard": [[{"text": "✅ Ejecutar", "callback_data": f"assist:{plan['id']}"}, {"text": "❌ No", "callback_data": "cancel"}]]}
        body = _esc(r["reply"]) + "\n\n<b>Plan:</b>\n" + "\n".join("• " + _esc(a["description"]) for a in plan["actions"])
        await self.notify.api("sendMessage", {"chat_id": self.notify.config.chat_id, "text": body[:3900], "parse_mode": "HTML", "reply_markup": kb})

    async def _ask_confirm(self, action: str, text: str) -> None:
        nonce = secrets.token_urlsafe(6)
        self.pending = {k: v for k, v in self.pending.items() if v[1] > time.time()}
        self.pending[nonce] = (action, time.time() + CONFIRM_TTL_S)
        kb = {"inline_keyboard": [[{"text": "✅ Sí, cerrar todo", "callback_data": f"{action}:{nonce}"}, {"text": "❌ No", "callback_data": "cancel"}]]}
        await self.notify.api("sendMessage", {"chat_id": self.notify.config.chat_id, "text": text, "parse_mode": "HTML", "reply_markup": kb})

    async def _callback(self, cq: dict) -> None:
        msg = cq.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id") or (cq.get("from") or {}).get("id")
        if not self._is_our_chat(chat_id):
            return
        data = str(cq.get("data") or "")
        await self.notify.api("answerCallbackQuery", {"callback_query_id": cq.get("id")})
        self.handled += 1
        edit = lambda t: self.notify.api("editMessageText", {"chat_id": chat_id, "message_id": msg.get("message_id"), "text": t, "parse_mode": "HTML"})  # noqa: E731
        if data == "cancel":
            await edit("Cancelado: no se ha cerrado nada.")
            return
        action, _, nonce = data.partition(":")
        p = self.pending.pop(nonce, None)
        if p is None or p[0] != action or p[1] < time.time():
            await edit("Esa confirmación caducó. Vuelve a pedirlo.")
            return
        if action == "flatten":
            await edit("✂️ Cerrando todo…")
            await self.notify.send(await self.flatten_all())
        elif action == "assist" and self.assistant is not None:
            await edit("⚙️ Ejecutando el plan…")
            try:
                r = await self.assistant.confirm(nonce)
                await self.notify.send("\n".join(("✅ " if x["ok"] else "❌ ") + _esc(x["action"]) + " → " + _esc(x["detail"]) for x in r["results"]) or "Nada que ejecutar.")
            except ValueError as exc:
                await self.notify.send(_esc(exc))

    # ---- acciones ----
    async def flatten_all(self) -> str:
        self.risk.set_kill_switch(True, "cerrar todo desde Telegram")
        done, errors = [], []
        for snap in list(self.accounts.accounts.values()):
            if snap.enabled and (snap.open_positions or snap.working_orders):
                try:
                    await self.risk.flatten(snap.account_id, reason="desde Telegram")
                    done.append(snap.account_id)
                except Exception as exc:
                    errors.append(f"{snap.account_id}: {exc}")
        out = "✂️ <b>Cierre total enviado</b>" + (f" en {', '.join(done)}" if done else ": no había posiciones ni órdenes") + ".\nKill switch activado; /reanudar para seguir."
        if errors:
            out += "\n❗ " + "\n❗ ".join(_esc(e) for e in errors)
        return out

    async def breakeven_all(self) -> str:
        if self.trade is None:
            return "Sin servicio de órdenes."
        lines = []
        for snap in list(self.accounts.accounts.values()):
            if not snap.enabled:
                continue
            for p in snap.open_positions:
                if not p.quantity:
                    continue
                last = self.market.price(p.symbol) if self.market is not None else 0
                try:
                    await self.trade.protect({"client_id": f"tg{secrets.token_hex(4)}", "account": snap.account_id, "symbol": p.symbol, "breakeven": True, "last_price": last or 0})
                    lines.append(f"✅ {snap.account_id} {p.symbol}: stop en {p.avg_price:g}")
                except Exception as exc:
                    lines.append(f"❌ {snap.account_id} {p.symbol}: {_esc(exc)}")
        return "🎯 <b>Breakeven</b>\n" + ("\n".join(lines) if lines else "No hay posiciones abiertas.")

    # ---- textos ----
    def status_text(self) -> str:
        rs = self.risk.state()
        h = self.bridge.health if self.bridge is not None else None
        accs = [a for a in self.accounts.accounts.values() if a.enabled]
        total = self.risk.day_total()
        head = f"📊 <b>Hoy {_usd(total)}</b> neto en {len(accs)} cuenta{'s' if len(accs) != 1 else ''}"
        flags = []
        flags.append("🛑 kill switch ACTIVO" + (f" ({_esc(rs.kill_switch_reason)})" if rs.kill_switch_reason else "") if rs.kill_switch else "▶️ copiando")
        if rs.day_closed:
            flags.append("⛔ día cerrado")
        if h is not None:
            flags.append(("📶 NinjaTrader conectado" if h.connected else "📵 sin NinjaTrader") + (f" · addon {h.addon_version}" if h.addon_version else "") + (f" · maestra {h.master_account}" if h.master_account else ""))
        rows = []
        for a in sorted(accs, key=lambda a: a.account_id):
            npos = sum(1 for p in a.open_positions if p.quantity)
            dd = a.drawdown.drawdown / a.drawdown.limit * 100 if a.drawdown.limit > 0 else None
            rows.append(f"• {_esc(a.alias or a.account_id)}: {_usd(a.net_pnl)}" + (f" · {npos} pos." if npos else "") + (f" · drawdown {dd:.0f} %" if dd is not None else ""))
        return "\n".join([head, " · ".join(flags)] + rows[:20] + ([f"… y {len(rows) - 20} más"] if len(rows) > 20 else []))

    def positions_text(self, short: bool = False) -> str:
        rows = []
        for a in self.accounts.accounts.values():
            if not a.enabled:
                continue
            for p in a.open_positions:
                if p.quantity:
                    rows.append(f"• {_esc(a.account_id)} {'L' if p.quantity > 0 else 'C'}{abs(p.quantity)} {_esc(p.symbol)} @ {p.avg_price:g}" + ("" if short else f" · flot. {_usd(p.unrealized_pnl)}"))
        if not rows:
            return "Sin posiciones abiertas."
        return ("" if short else "📈 <b>Posiciones</b>\n") + "\n".join(rows[:30])
