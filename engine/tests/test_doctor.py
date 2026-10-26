"""Robustez 5: doctor y zip de diagnóstico sin secretos."""
import io
import json
import zipfile

from tradepilot import doctor
from tradepilot.services.diagnostics import build_zip, redact


def test_doctor_checks_and_summary(container):
    checks = doctor.run_checks(container.settings, store=container.store, accounts=container.accounts, replication=container.replication, risk=container.risk, in_engine=True)
    titles = {c["title"] for c in checks}
    assert any(t.startswith("Python") for t in titles) and "API_TOKEN" in titles and any("Modo mock" in t for t in titles)
    tok = next(c for c in checks if c["title"] == "API_TOKEN")
    assert tok["level"] == "bad"                                   # "test-token" es corto
    s = doctor.summary(checks)
    assert s["bad"] >= 1 and sum(s.values()) == len(checks)
    assert all(c["level"] in ("ok", "warn", "bad", "info") for c in checks)


def test_redact_removes_tokens():
    out = redact("Authorization: Bearer abcdef123456 token=xyz987 bot123456789:AAHhh-token secreto-largo", ["secreto-largo"])
    assert "abcdef123456" not in out and "xyz987" not in out and "AAHhh" not in out and "secreto-largo" not in out and out.count("***") >= 4


async def test_diagnostics_zip_has_no_secrets(client, container):
    from tradepilot.services.notify_service import NotifyConfig
    container.notify.set_config(NotifyConfig(enabled=False, bot_token="123456789:AAH-super-secreto", chat_id="4242424242"))
    container.audit.log("KILL_SWITCH_ON", "prueba con token test-token dentro del mensaje")
    data = build_zip(container)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    assert {"summary.json", "accounts.json", "rules.json", "audit_24h.json", "doctor.json", "doctor.txt", "manual_orders_today.json"} <= set(names)
    blob = b"".join(z.read(n) for n in names).decode("utf-8", "replace")
    assert "test-token" not in blob and "AAH-super-secreto" not in blob and "4242424242" not in blob
    summ = json.loads(z.read("summary.json"))
    assert summ["settings"]["API_TOKEN"] == "***" and summ["bridge"]["mode"] == "mock" and "watchdog" in summ
    audits = json.loads(z.read("audit_24h.json"))
    assert any(a["event_type"] == "KILL_SWITCH_ON" and "***" in a["message"] for a in audits)
    # API
    r = await client.get("/api/doctor")
    assert r.status_code == 200 and r.json()["counts"]["bad"] >= 1
    r = await client.get("/api/diagnostics.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip" and "attachment" in r.headers["content-disposition"]
    assert any(a.event_type == "DIAGNOSTICS" for a in container.audit.recent(10))
