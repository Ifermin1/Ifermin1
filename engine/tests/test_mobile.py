"""Punto 5 (móvil): Web Push desde el engine y órdenes por Telegram."""
import asyncio

from tradepilot.services.notify_service import NotifyConfig

SUB = {"endpoint": "https://push.example.com/abc", "keys": {"p256dh": "BPub", "auth": "auth"}}
SUB2 = {"endpoint": "https://push.example.com/def", "keys": {"p256dh": "BPub2", "auth": "auth2"}}


class FakePush:
    """Simula pywebpush: devuelve un status por endpoint; 410 = suscripción caducada."""
    def __init__(self):
        self.sent = []
        self.status = {}

    def __call__(self, sub, payload):
        self.sent.append((sub["endpoint"], payload))
        return self.status.get(sub["endpoint"], 201)


async def test_push_keys_subscriptions_and_broadcast(container):
    push = container.push
    assert push.available and len(push.public_key) > 60 and push.private_key      # claves VAPID generadas y guardadas
    fp = FakePush()
    push.sender = fp
    st = push.subscribe(SUB, "Android Chrome")
    push.subscribe(SUB2, "iPhone")
    assert st["subscriptions"] == 1 and push.state()["subscriptions"] == 2
    try:
        push.subscribe({"endpoint": "http://inseguro", "keys": {}})
        assert False
    except ValueError:
        pass
    # un evento marcado para avisar llega a los dos teléfonos; uno que no está marcado, no
    container.audit.log("DAILY_LOSS_LIMIT", "Sim102: pérdida diaria", target="Sim102")
    container.audit.log("FOLLOWER_FILL", "Sim102: BUY 1 NQ", target="Sim102")
    await asyncio.sleep(0.1)
    assert len(fp.sent) == 2 and all("Límite de pérdida diaria" in p for _, p in fp.sent) and '"url": "/#pocket"' in fp.sent[0][1]
    # repetido en menos de un minuto: no se reenvía
    container.audit.log("DAILY_LOSS_LIMIT", "Sim102: pérdida diaria", target="Sim102")
    await asyncio.sleep(0.05)
    assert len(fp.sent) == 2
    # la suscripción caducada (410) se elimina sola al probar
    fp.status[SUB2["endpoint"]] = 410
    r = await push.test()
    assert r["ok"] is True and r["sent"] == 1 and r["removed"] == 1 and push.state()["subscriptions"] == 1
    assert push.unsubscribe(SUB["endpoint"])["subscriptions"] == 0
    assert (await push.test())["ok"] is False


async def test_push_api(client, container):
    container.push.sender = FakePush()
    r = await client.get("/api/push")
    assert r.status_code == 200 and r.json()["public_key"] and r.json()["subscriptions"] == 0
    r = await client.post("/api/push/subscribe", json={"subscription": SUB, "ua": "test"})
    assert r.status_code == 200 and r.json()["subscriptions"] == 1
    assert (await client.post("/api/push/subscribe", json={"subscription": {"endpoint": "x"}})).status_code == 400
    r = await client.post("/api/push/test")
    assert r.json()["ok"] is True
    assert (await client.post("/api/push/unsubscribe", json={"endpoint": SUB["endpoint"]})).json()["subscriptions"] == 0
    assert any(a["event_type"] == "PUSH_SUBSCRIBED" for a in (await client.get("/api/audit?limit=50")).json())


class FakeBot:
    """Telegram falso: entrega los updates encolados una sola vez por sondeo y guarda todo lo que el engine manda."""
    def __init__(self):
        self.calls = []
        self.queue = []

    async def __call__(self, token, method, payload):
        self.calls.append((method, payload))
        if method == "getUpdates":
            out, self.queue = self.queue, []
            return {"ok": True, "result": out}
        return {"ok": True, "result": {"message_id": 7}}

    def texts(self, method="sendMessage"):
        return [p.get("text", "") for m, p in self.calls if m == method]


async def _wait(cond, timeout=2.0):
    t0 = asyncio.get_running_loop().time()
    while not cond():
        assert asyncio.get_running_loop().time() - t0 < timeout, "timeout"
        await asyncio.sleep(0.02)


async def test_telegram_commands(container):
    await container.start()
    try:
        n = container.notify
        bot = FakeBot()
        n.sender = bot
        n.poll_idle_s = 0.02
        n.set_config(NotifyConfig(enabled=True, bot_token="t", chat_id="42"))
        assert n._poll_task is not None
        await container.accounts.sync_once()
        await container.trade.place({"client_id": "m1", "account": "Sim101", "symbol": "NQ 12-26", "action": "BUY", "quantity": 2, "order_type": "MARKET", "sl_ticks": 0, "tp_ticks": 0})
        await container.accounts.sync_once()
        msg = lambda text, chat="42", uid=1: {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}  # noqa: E731
        # otro chat: ignorado; /estado y /posiciones responden
        bot.queue = [msg("/estado", chat="999", uid=1), msg("/estado", uid=2), msg("/posiciones", uid=3)]
        await _wait(lambda: len(bot.texts()) >= 2)
        assert "Hoy" in bot.texts()[0] and "copiando" in bot.texts()[0] and "Sim101" in bot.texts()[0]
        assert "L2 NQ 12-26" in bot.texts()[1]
        assert n.commands.handled == 2
        # /pausar y /reanudar mueven el kill switch
        bot.queue = [msg("/pausar", uid=4)]
        await _wait(lambda: container.risk.kill_switch)
        bot.queue = [msg("/reanudar", uid=5)]
        await _wait(lambda: not container.risk.kill_switch)
        # /breakeven pone el stop en la entrada
        bot.queue = [msg("/breakeven", uid=6)]
        await _wait(lambda: any("Breakeven" in t for t in bot.texts()))
        assert any("✅ Sim101 NQ 12-26" in t for t in bot.texts())
        # /cerrar pide confirmación con botones; la confirmación caducada no hace nada; la buena cierra y activa el kill switch
        bot.queue = [msg("/cerrar", uid=7)]
        await _wait(lambda: any("reply_markup" in p for m, p in bot.calls if m == "sendMessage"))
        kb = next(p["reply_markup"] for m, p in bot.calls if m == "sendMessage" and "reply_markup" in p)
        data = kb["inline_keyboard"][0][0]["callback_data"]
        cb = lambda d, uid: {"update_id": uid, "callback_query": {"id": "cq", "data": d, "from": {"id": 42}, "message": {"message_id": 7, "chat": {"id": 42}}}}  # noqa: E731
        bot.queue = [cb("flatten:caducado", 8)]
        await _wait(lambda: any("caducó" in t for t in bot.texts("editMessageText")))
        assert container.accounts.find("Sim101").open_positions and not container.risk.kill_switch
        bot.queue = [cb(data, 9)]
        await _wait(lambda: any("Cierre total enviado" in t for t in bot.texts()))
        await container.accounts.sync_once()
        assert not any(p.quantity for p in container.accounts.find("Sim101").open_positions) and container.risk.kill_switch
        assert any(m == "answerCallbackQuery" for m, _ in bot.calls)
        # desactivar para el sondeo
        n.set_config(NotifyConfig(enabled=False, bot_token="t", chat_id="42"))
        await asyncio.sleep(0.05)
        assert n._poll_task is None
    finally:
        await container.stop()
