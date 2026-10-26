"""Robustez 4: copias de seguridad y exportar/importar configuración."""
import json
import sqlite3

from tradepilot.container import build_container
from tradepilot.core.config import Settings
from tradepilot.core.events import EventBus
from tradepilot.domain.risk import RiskLimit
from tradepilot.infrastructure.brokers.mock import MockBridge


def _build(db, **kw):
    cfg = Settings(ENGINE_MODE="mock", API_TOKEN="t", DB_PATH=str(db), WEB_DIST="/nonexistent", MOCK_DEMO_HISTORY=False, **kw)
    return build_container(cfg, bridge=MockBridge(EventBus(), emit_events=False))


async def test_backup_rotation_and_integrity(tmp_path):
    c = _build(tmp_path / "t.db", BACKUP_KEEP=2)
    b = c.backup
    assert b.available and b.check_integrity() == "ok"
    c.replication.add_rule("Sim101", "Sim102", 2.0)
    r1 = b.backup_now("prueba")
    assert (tmp_path / "backups" / r1["name"]).exists() and r1["size"] > 0
    import time; time.sleep(1.1)
    b.backup_now("prueba"); time.sleep(1.1)
    r3 = b.backup_now("prueba")
    st = b.state()
    assert len(st["files"]) == 2 and st["files"][0]["name"] == r3["name"] and r3["removed"] == 1 and st["last_at"]
    # la copia es una base legible con las reglas dentro
    con = sqlite3.connect(str(tmp_path / "backups" / r3["name"]))
    assert con.execute("SELECT COUNT(*) FROM replication_rules").fetchone()[0] == 1
    con.close()
    assert b.path_of("../x.db") is None and b.path_of(r3["name"]) is not None
    assert any(a.event_type == "BACKUP" for a in c.audit.recent(10))
    c.store.close()


async def test_export_import_roundtrip(tmp_path):
    src = _build(tmp_path / "a.db")
    await src.accounts.sync_once()
    src.replication.add_rule("Sim101", "Sim102", 3.0, None, True, entry_mode="limit", tolerance_ticks=1)
    src.risk.upsert_limit(RiskLimit(account_id="Sim102", max_daily_loss=500, max_position_size=3))
    src.risk.set_day_loss(1200)
    await src.accounts.set_settings("Sim102", alias="Apex 50K", firm="apex", plan="eval", plan_size=50000, tags=["50K"])
    src.store.set_kv("ui.trade_templates", json.dumps([{"id": "t1", "name": "NQ 1", "symbol": "NQ 12-26", "quantity": 1, "order_type": "MARKET", "sl_ticks": 40, "tp_ticks": 80, "trail": None}]))
    data = src.backup.export_config()
    assert data["version"] == 1 and len(data["rules"]) == 1 and data["rules"][0]["entry_mode"] == "limit" and data["day_loss_limit"] == 1200
    assert not any(k in json.dumps(data) for k in ("bot_token", "chat_id", "API_TOKEN")), "la exportación no lleva secretos"
    acc = next(a for a in data["accounts"] if a["account_id"] == "Sim102")
    assert acc["alias"] == "Apex 50K" and acc["tags"] == ["50K"]
    src.store.close()
    dst = _build(tmp_path / "b.db")
    await dst.accounts.sync_once()
    done = await dst.backup.import_config(json.loads(json.dumps(data)))
    assert done["rules"] == 1 and done["risk_limits"] == 1 and done["accounts"] >= 1 and "plantillas" in done["other"]
    r = dst.replication.rules[0]
    assert r.master_account == "Sim101" and r.follower_account == "Sim102" and r.multiplier == 3.0 and r.entry_mode == "limit" and r.tolerance_ticks == 1
    assert dst.risk.limits["Sim102"].max_daily_loss == 500 and dst.risk.day_loss_limit == 1200
    assert dst.accounts.find("Sim102").alias == "Apex 50K" and dst.accounts.find("Sim102").tags == ["50K"]
    assert json.loads(dst.store.get_kv("ui.trade_templates"))[0]["name"] == "NQ 1"
    # importar dos veces no duplica reglas
    await dst.backup.import_config(data)
    assert len(dst.replication.rules) == 1
    assert any(a.event_type == "CONFIG_IMPORTED" for a in dst.audit.recent(10))
    dst.store.close()


async def test_backup_and_config_api(client, container):
    r = await client.get("/api/backup")
    assert r.status_code == 200 and r.json()["available"] is False          # :memory: en los tests
    assert (await client.post("/api/backup/run")).status_code == 400
    r = await client.get("/api/config/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "rules" in r.json()
    r = await client.post("/api/config/import", json={"data": {"version": 1, "day_loss_limit": 777}})
    assert r.status_code == 200 and container.risk.day_loss_limit == 777
    assert (await client.post("/api/config/import", json={"data": {"version": 99}})).status_code == 422
