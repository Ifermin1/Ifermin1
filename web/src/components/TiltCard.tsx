import { useEffect, useState } from "react";
import type { Client, TiltState } from "../lib/api";
import { useStore } from "../lib/store";
import { Card } from "./ui";

/* Detector de tilt: señales por cuenta (racha, tamaño creciente, reentradas, frecuencia, hundimiento), aviso y pausa
 * automática opcional. */
export function TiltCard({ client }: { client: Client }) {
  const { readOnly } = useStore();
  const [st, setSt] = useState<TiltState | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => client.tilt().then(setSt).catch(() => {});
  useEffect(() => { void load(); const t = window.setInterval(load, 30000); return () => window.clearInterval(t); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  const cfg = async (c: Partial<TiltState["config"]>) => { try { setSt(await client.tiltConfig(c)); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } };
  const resume = async (acc: string) => { try { const r = await client.tiltResume(acc); setSt(r); setMsg(r.resumed ? `${acc}: pausa levantada.` : `${acc} no estaba pausada por tilt.`); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } };
  const c = st?.config;
  const hot = Object.entries(st?.accounts ?? {}).filter(([, v]) => v.score >= 2);
  return (
    <Card title="Detector de tilt" right={st && <span className={`badge ${hot.length ? "bad" : c?.enabled ? "ok" : ""}`}>{!c?.enabled ? "desactivado" : hot.length ? `${hot.length} cuenta${hot.length === 1 ? "" : "s"} en tilt` : "vigilando"}</span>}>
      {!st || !c ? <p className="muted small">Cargando…</p> : (
        <>
          <p className="muted small">Con cada operación cerrada mira si estás operando por rabia: <b>{c.streak} pérdidas seguidas</b>, tamaño creciente tras perder, reentradas en menos de {c.reentry_s} s, más de {c.max_per_hour} operaciones por hora, o un día que iba en positivo y cae más de {c.day_drop_usd} $. Con dos señales avisa al teléfono{c.auto_pause_min > 0 ? ` y pausa la cuenta ${c.auto_pause_min} min` : ""}.</p>
          <div className="chips small">
            <label className="inline small"><input type="checkbox" checked={c.enabled} disabled={readOnly} onChange={(e) => void cfg({ enabled: e.target.checked })} data-testid="tilt-enabled" /> activo</label>
            <label className="inline small">racha <input type="number" min={2} max={10} value={c.streak} disabled={readOnly} onChange={(e) => void cfg({ streak: Number(e.target.value) })} style={{ width: 56 }} /></label>
            <label className="inline small">pausa automática <select value={c.auto_pause_min} disabled={readOnly} onChange={(e) => void cfg({ auto_pause_min: Number(e.target.value) })} data-testid="tilt-pause">{[0, 10, 15, 30, 60].map((m) => <option key={m} value={m}>{m === 0 ? "solo avisar" : `${m} min`}</option>)}</select></label>
            <span className="muted">{st.ai ? "aviso escrito por la IA" : "aviso por reglas"}</span>
          </div>
          {Object.keys(st.paused).length > 0 && <ul className="alerts" style={{ marginTop: 8 }}>{Object.entries(st.paused).map(([acc, until]) => <li key={acc} className="alert bad"><div className="alert-main"><b>{acc} pausada por tilt</b><span className="muted">hasta las {until.slice(11, 16)}</span></div><div className="chips"><button className="chip-btn" disabled={readOnly} onClick={() => void resume(acc)}>Reanudar ya</button></div></li>)}</ul>}
          {hot.length > 0 && <ul className="alerts" style={{ marginTop: 8 }} data-testid="tilt-list">{hot.map(([acc, v]) => <li key={acc} className="alert warn"><div className="alert-main"><b>{acc} · {v.signals.map((s) => s.text).join(" · ")}</b><span className="muted">{v.at.slice(11, 16)}</span></div></li>)}</ul>}
          {msg && <p className="muted small">{msg}</p>}
        </>
      )}
    </Card>
  );
}
