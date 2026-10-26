import { useEffect, useState } from "react";
import type { Client, DoctorCheck } from "../lib/api";
import { useStore } from "../lib/store";
import { Card } from "./ui";

/* Doctor (revisión de la instalación) y zip de diagnóstico para pedir ayuda sin pasar secretos. */
const ICON: Record<DoctorCheck["level"], string> = { ok: "✔", warn: "!", bad: "✖", info: "·" };

export function DoctorCard({ client }: { client: Client }) {
  const { readOnly } = useStore();
  const [data, setData] = useState<{ checks: DoctorCheck[]; counts: Record<string, number> } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [showOk, setShowOk] = useState(false);
  const load = async () => { setBusy("doctor"); try { setData(await client.doctor()); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); } };
  useEffect(() => { void load(); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  const zip = async () => {
    setBusy("zip"); setMsg(null);
    try { const { blob, filename } = await client.diagnosticsZip(); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = filename; a.click(); window.setTimeout(() => URL.revokeObjectURL(a.href), 2000); setMsg(`Diagnóstico guardado (${Math.round(blob.size / 1024)} KB): logs, auditoría de 24 h, salud y configuración con los tokens tachados. Envíalo a quien te ayude.`); }
    catch (ex) { setMsg((ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "")); } finally { setBusy(null); }
  };
  const list = (data?.checks ?? []).filter((c) => showOk || c.level !== "ok");
  const c = data?.counts;
  return (
    <Card title="Revisión de la instalación (doctor)" right={c && <span className={`badge ${c.bad ? "bad" : c.warn ? "warn" : "ok"}`}>{c.bad ? `${c.bad} problema${c.bad === 1 ? "" : "s"}` : c.warn ? `${c.warn} aviso${c.warn === 1 ? "" : "s"}` : "todo bien"}</span>}>
      <div className="chips" style={{ marginBottom: 8 }}>
        <button className="small-btn" disabled={busy !== null} onClick={() => void load()} data-testid="doctor-run">{busy === "doctor" ? "Revisando…" : "Revisar de nuevo"}</button>
        <button className="primary small-btn" disabled={busy !== null || readOnly} onClick={() => void zip()} data-testid="diag-zip">{busy === "zip" ? "Generando…" : "Guardar diagnóstico (zip)"}</button>
        <label className="inline small"><input type="checkbox" checked={showOk} onChange={(e) => setShowOk(e.target.checked)} /> ver también lo que está bien</label>
        <span className="muted small">también en el PC: <code>python -m tradepilot.doctor</code></span>
      </div>
      {!data ? <p className="muted small">Revisando…</p> : list.length === 0 ? <p className="muted small">Sin avisos ni problemas.</p> : (
        <ul className="doctor-list" data-testid="doctor-list">
          {list.map((x, i) => <li key={i} className={x.level}><span className="doctor-icon">{ICON[x.level]}</span><b>{x.title}</b>{x.detail && <span className="muted"> — {x.detail}</span>}</li>)}
        </ul>
      )}
      {msg && <p className="muted small" data-testid="doctor-msg">{msg}</p>}
    </Card>
  );
}
