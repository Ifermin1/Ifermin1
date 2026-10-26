import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import type { ReconcileFinding, ReconcileReport } from "../lib/api";
import { Card, Empty } from "./ui";
import type { Route } from "./Shell";

/* Reconciliación: lo que no cuadra entre lo que el engine cree y lo que NinjaTrader reporta (al arrancar y a petición),
 * con el botón que lo resuelve. */
const TONE: Record<ReconcileFinding["severity"], string> = { bad: "bad", warn: "warn", info: "info" };
const LABEL: Record<ReconcileFinding["kind"], string> = { unconfirmed_order: "Orden sin confirmar", naked_position: "Posición sin stop", pending_entry: "Entrada pendiente", desync: "Desincronizada", trail_without_position: "Trailing sin posición" };

export function ReconcileCard({ onRoute }: { onRoute?: (r: Route) => void }) {
  const { client, guard, refresh, readOnly } = useStore();
  const [rep, setRep] = useState<ReconcileReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [hideInfo, setHideInfo] = useState(true);
  const load = () => client?.reconcile().then(setRep).catch(() => {});
  useEffect(() => { if (!client) return; void load(); const t = window.setInterval(load, 30000); return () => window.clearInterval(t); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  const run = async () => { if (!client) return; setBusy("run"); setMsg(null); try { setRep(await client.reconcileRun()); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); } };
  const resync = async (acc: string) => {
    if (!client || !(await guard()) || !confirm(`¿Igualar ${acc} a la maestra? Se manda a mercado la diferencia de contratos.`)) return;
    setBusy(acc); setMsg(null);
    try { await client.resync(acc); setMsg(`${acc}: orden de igualar enviada.`); await refresh(); await run(); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  };
  const list = (rep?.findings ?? []).filter((f) => !hideInfo || f.severity !== "info");
  const bad = rep?.counts.bad ?? 0, warn = rep?.counts.warn ?? 0;
  return (
    <Card title={`Reconciliación${rep?.ran_at ? ` · ${rep.reason === "arranque" ? "al arrancar" : "a petición"} ${rep.ran_at.slice(11, 16)}` : ""}`} icon="shield"
          right={<span className="chips"><label className="inline small"><input type="checkbox" checked={hideInfo} onChange={(e) => setHideInfo(e.target.checked)} /> ocultar informativas</label><button className="chip-btn" disabled={busy !== null} onClick={() => void run()} data-testid="reconcile-run">{busy === "run" ? "…" : "Volver a comprobar"}</button></span>}
          className={bad ? "alerts-card has-bad" : ""}>
      {!rep ? <p className="muted small">Cargando…</p> : !rep.ran_at ? <p className="muted small">Aún no se ha reconciliado: se hace sola en la primera sincronización con NinjaTrader.</p>
        : list.length === 0 ? <Empty>{bad + warn === 0 ? "Todo cuadra: posiciones protegidas, sin órdenes a medias ni desincronizaciones." : "Solo hallazgos informativos ocultos."}</Empty> : (
        <ul className="alerts" data-testid="reconcile-list">
          {list.map((f, i) => (
            <li key={i} className={`alert ${TONE[f.severity]}`} data-testid={`reconcile-${f.kind}`}>
              <div className="alert-main"><b>{f.account} · {LABEL[f.kind]}</b><span className="muted">{f.detail}</span></div>
              <div className="chips">
                {f.action === "protect" && <button className="chip-btn" onClick={() => onRoute?.("trade")}>Proteger en Operar</button>}
                {f.action === "resync" && <button className="chip-btn" disabled={busy !== null || readOnly} onClick={() => void resync(f.account)}>{busy === f.account ? "…" : "Igualar"}</button>}
                {f.action === "review" && f.kind === "unconfirmed_order" && <button className="chip-btn" onClick={() => onRoute?.("trade")}>Ver en Operar</button>}
                {f.action === "review" && f.kind !== "unconfirmed_order" && <button className="chip-btn" onClick={() => onRoute?.("trade")}>Revisar</button>}
              </div>
            </li>
          ))}
        </ul>
      )}
      {msg && <p className="small">{msg}</p>}
    </Card>
  );
}
