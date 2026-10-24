import { useEffect, useState } from "react";
import type { Client, HubState } from "../lib/api";
import { Card } from "./ui";

/* Hub: estado del envío de este engine al servidor central (varios PCs en un sitio). Solo lectura + "Enviar ahora". */
export function HubCard({ client }: { client: Client }) {
  const [st, setSt] = useState<HubState | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => client.hub().then(setSt).catch(() => {});
  useEffect(() => { void load(); const t = window.setInterval(load, 30000); return () => window.clearInterval(t); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  const flush = async () => { setBusy(true); setMsg(null); try { const r = await client.hubFlush(); setSt(r); setMsg(r.ok ? `Enviados ${r.sent} elementos.` : `No se pudo enviar: ${r.error}`); } catch (ex) { setMsg((ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "")); } finally { setBusy(false); } };
  const ago = (t: number | null) => (t ? `hace ${Math.max(0, Math.round((Date.now() / 1000 - t) / 60))} min` : "nunca");
  return (
    <Card title="Hub (varios engines en un sitio)" right={st && <span className={`badge ${st.enabled ? (st.stats.last_error ? "bad" : "ok") : ""}`}>{st.enabled ? (st.stats.last_error ? "con errores" : "activo") : "sin configurar"}</span>}>
      {!st ? <p className="muted small">Cargando…</p> : !st.enabled ? (
        <p className="muted small">Opcional. Si operas desde varios PCs o quieres el histórico de todas las cuentas en un solo sitio, arranca el hub (carpeta <code>hub/</code>, ver <code>hub/README.md</code>) y pon en <code>engine\.env</code> <code>HUB_URL</code>, <code>HUB_TOKEN</code> y <code>HUB_NODE</code>. Este engine le mandará operaciones, estado y avisos; si el hub está caído se guardan y se reintentan.</p>
      ) : (
        <div className="chips" data-testid="hub-state">
          <span className="small">{st.node} → <code>{st.url}</code> · cada {st.interval_s} s</span>
          <span className="muted small">{st.queued} en cola · {st.stats.sent} enviados en {st.stats.batches} lotes · último envío {ago(st.stats.last_ok_at)}{st.stats.dropped ? ` · ${st.stats.dropped} descartados por cola llena` : ""}</span>
          {st.stats.last_error && <span className="bad small">último error: {st.stats.last_error}</span>}
          <button className="chip-btn" disabled={busy} onClick={() => void flush()} data-testid="hub-flush">{busy ? "…" : "Enviar ahora"}</button>
        </div>
      )}
      {msg && <p className="muted small">{msg}</p>}
    </Card>
  );
}
