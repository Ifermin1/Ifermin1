import { useEffect, useState } from "react";
import type { Client, DigestState } from "../lib/api";
import { useStore } from "../lib/store";
import { Card } from "./ui";

/* Briefing de la mañana y cierre de sesión: se generan solos a la hora elegida y llegan al teléfono; aquí se leen y se
 * pueden generar al momento. */
export function DigestCard({ client }: { client: Client }) {
  const { readOnly } = useStore();
  const [st, setSt] = useState<DigestState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [tab, setTab] = useState<"brief" | "review">(() => (new Date().getHours() < 14 ? "brief" : "review"));
  const load = () => client.digest().then(setSt).catch(() => {});
  useEffect(() => { void load(); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (kind: "brief" | "review") => { setBusy(kind); try { await client.digestRun(kind); await load(); } finally { setBusy(null); } };
  const cfg = async (c: Partial<DigestState["config"]>) => { try { setSt(await client.digestConfig(c)); } catch { /* sin permiso */ } };
  const cur = st ? st[tab] : null;
  return (
    <Card title="Briefing y cierre" icon="calendar" right={st && <span className="chips">
      <div className="view-tabs"><button className={tab === "brief" ? "active" : ""} onClick={() => setTab("brief")}>Mañana</button><button className={tab === "review" ? "active" : ""} onClick={() => setTab("review")}>Cierre</button></div>
      <button className="chip-btn" disabled={busy !== null || readOnly} onClick={() => void run(tab)} data-testid="digest-run">{busy === tab ? "…" : "Generar ahora"}</button></span>}>
      {!st ? <p className="muted small">Cargando…</p> : (
        <>
          <pre className="digest-text" data-testid="digest-text">{cur ? cur.text : tab === "brief" ? "Aún no hay briefing de hoy." : "Aún no hay cierre de hoy."}</pre>
          <div className="chips small">
            <label className="inline small"><input type="checkbox" checked={st.config.enabled} disabled={readOnly} onChange={(e) => void cfg({ enabled: e.target.checked })} /> automáticos</label>
            <label className="inline small">briefing a las <input type="time" value={st.config.brief_time} disabled={readOnly} onChange={(e) => void cfg({ brief_time: e.target.value })} data-testid="digest-brief-time" /></label>
            <label className="inline small">cierre a las <input type="time" value={st.config.review_time} disabled={readOnly} onChange={(e) => void cfg({ review_time: e.target.value })} /></label>
            <label className="inline small"><input type="checkbox" checked={st.config.weekdays_only} disabled={readOnly} onChange={(e) => void cfg({ weekdays_only: e.target.checked })} /> solo lunes a viernes</label>
            <span className="muted">{st.ai ? "con IA" : "por reglas (con clave de IA, Claude escribe el briefing)"} · llegan por Telegram/push</span>
          </div>
        </>
      )}
    </Card>
  );
}
