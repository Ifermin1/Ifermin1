import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import type { Guardian, GuardianAccount } from "../lib/api";
import { Card, Empty } from "./ui";
import { signedMoney } from "../lib/format";
import type { Route } from "./Shell";

/* Gestor preventivo: cada cuenta con su dueño, su prop firm y sus reglas, en un semáforo con la causa, el valor, el
 * límite, el margen y la acción; cartera consolidada y choques de −10/−20/−30/−50 puntos del NQ. */
const ICON = { green: "🟢", amber: "🟡", red: "🔴" } as const;
const usd0 = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${Math.round(n).toLocaleString("es-ES")} $`);

export function GuardianCard({ onRoute, compact = false }: { onRoute?: (r: Route) => void; compact?: boolean }) {
  const { client, readOnly, guard } = useStore();
  const [g, setG] = useState<Guardian | null>(null);
  const [owner, setOwner] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [cfgOpen, setCfgOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = () => client?.guardian(owner).then(setG).catch(() => {});
  useEffect(() => { void load(); const t = window.setInterval(load, 15000); return () => window.clearInterval(t); }, [client, owner]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!client) return null;
  const save = async (changes: Record<string, number | boolean>) => {
    if (changes.auto_flatten_mae !== undefined && !(await guard())) return;
    setBusy(true); setErr(null);
    try { setG(await client.guardianSet(changes)); } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(false); }
  };
  const tone = g ? (g.counts.red ? "bad" : g.counts.amber ? "warn" : "ok") : "";
  const rows = g?.accounts ?? [];
  const shown = compact ? rows.filter((r) => r.status !== "green") : rows;
  return (
    <Card title={`Gestor preventivo${g ? ` · ${ICON.green} ${g.counts.green} · ${ICON.amber} ${g.counts.amber} · ${ICON.red} ${g.counts.red}` : ""}`} icon="shield" className={`guardian ${tone === "bad" ? "alerts-card has-bad" : ""}`}
          right={<span className="chips">
            {g && g.owners.length > 0 && <select value={owner} onChange={(e) => setOwner(e.target.value)} data-testid="guardian-owner"><option value="">todos los dueños</option>{g.owners.map((o) => <option key={o} value={o}>{o}</option>)}</select>}
            <button className="chip-btn" onClick={() => setCfgOpen(!cfgOpen)}>{cfgOpen ? "cerrar límites" : "límites preventivos"}</button>
            {onRoute && <button className="chip-btn" onClick={() => onRoute("accounts")}>Cuentas</button>}
          </span>}>
      {!g ? <p className="muted small">Cargando…</p> : (
        <>
          <p className={`guardian-verdict ${tone}`} data-testid="guardian-verdict"><b>{g.verdict}</b>
            <span className="muted small"> · cartera{owner ? ` de ${owner}` : ""}: equity {usd0(g.totals.equity)} · margen hasta liquidación {usd0(g.totals.room)} · flotante {signedMoney(g.totals.floating)} · neto hoy {signedMoney(g.totals.net_pnl)} · {g.totals.open_contracts} contr. abiertos ({g.totals.nq_equiv} NQ equiv.)</span>
            {g.unassigned > 0 && !owner && <span className="muted small"> · {g.unassigned} sin dueño (ponlo en Cuentas → detalle)</span>}
          </p>
          {g.totals.open_contracts > 0 && (
            <div className="chips small" data-testid="guardian-shocks"><span className="muted">si el NQ cae</span>
              {g.shocks.map((s) => <span key={s.points} className={`chip ${s.liquidated.length ? "bad" : ""}`} title={s.liquidated.length ? `liquidaría: ${s.liquidated.join(", ")}` : "ninguna cuenta tocaría su suelo"}>−{s.points} pts → {signedMoney(s.pnl)}{s.liquidated.length ? ` · ${s.liquidated.length} liquidada${s.liquidated.length > 1 ? "s" : ""}` : ""}</span>)}
            </div>
          )}
          {cfgOpen && (
            <div className="rule-form guardian-cfg" data-testid="guardian-cfg">
              <label>MAE oficial (% del beneficio de referencia)<input inputMode="decimal" defaultValue={Math.round(g.config.mae_pct * 100)} onBlur={(e) => void save({ mae_pct: (Number(e.target.value) || 30) / 100 })} /></label>
              <label>MAE preventivo (%, aviso)<input inputMode="decimal" defaultValue={Math.round(g.config.mae_prevent_pct * 100)} onBlur={(e) => void save({ mae_prevent_pct: (Number(e.target.value) || 20) / 100 })} /></label>
              <label>Suelo del límite MAE ($, cuentas con poco beneficio)<input inputMode="decimal" defaultValue={g.config.mae_floor_usd} onBlur={(e) => void save({ mae_floor_usd: Number(e.target.value) || 0 })} /></label>
              <label>Riesgo/beneficio máximo (X:1)<input inputMode="decimal" defaultValue={g.config.rr_max} onBlur={(e) => void save({ rr_max: Number(e.target.value) || 5 })} /></label>
              <label>Inactividad (días)<input inputMode="numeric" defaultValue={g.config.inactivity_days} onBlur={(e) => void save({ inactivity_days: Number(e.target.value) || 30 })} /></label>
              <label className="inline"><input type="checkbox" checked={g.config.auto_flatten_mae} disabled={busy || readOnly} onChange={(e) => void save({ auto_flatten_mae: e.target.checked })} /> cerrar la posición al superar el MAE oficial</label>
              <p className="muted small" style={{ gridColumn: "1 / -1", margin: 0 }}>Oficial = regla de la firma (fuente en cada alerta). Preventivo = tu límite, más estricto. El gestor nunca opera salvo el cierre por MAE si lo activas; lo demás son avisos (Auditoría y teléfono).</p>
            </div>
          )}
          {shown.length === 0 ? <Empty>{compact ? "Todas las cuentas en verde." : "Sin cuentas activas."}</Empty> : (
            <div className="table-wrap">
              <table className="guardian-table" data-testid="guardian-table">
                <thead><tr><th>Cuenta</th><th className="num">Balance</th><th className="num">Equity</th><th className="num">Liquidación</th><th className="num">Buffer</th><th className="num">MAE</th><th>Consistencia</th><th className="num">Contratos</th><th>Retiro</th><th>Estado</th></tr></thead>
                <tbody>
                  {shown.map((r: GuardianAccount) => (
                    <>
                      <tr key={r.account_id} className={`st-${r.status}`} onClick={() => setOpen(open === r.account_id ? null : r.account_id)} data-testid={`guardian-${r.account_id}`}>
                        <td><b>{r.alias || r.account_id}</b><div className="muted small">{[r.owner && `👤 ${r.owner}`, r.firm && `${r.firm}${r.plan ? ` · ${r.plan}` : ""}`].filter(Boolean).join(" · ") || "sin dueño ni prop firm"}</div></td>
                        <td className="num">{usd0(r.balance)}</td>
                        <td className="num">{usd0(r.equity)}</td>
                        <td className="num">{r.floor !== null ? `${usd0(r.floor)}${r.dd_locked ? " 🔒" : ""}` : "—"}</td>
                        <td className={`num ${r.room !== null && r.dd_pct !== null && r.dd_pct >= 80 ? "bad" : ""}`}>{r.room !== null ? `${usd0(r.room)}${r.dd_pct !== null ? ` (${(100 - r.dd_pct).toFixed(0)} %)` : ""}` : "—"}</td>
                        <td className={`num ${r.mae.pct !== null && r.mae.pct >= 100 ? "bad" : r.mae.pct !== null && r.mae.pct >= 60 ? "warn" : ""}`}>{r.mae.pct !== null ? `${r.mae.pct.toFixed(0)} % de ${usd0(r.mae.limit)}` : r.mae.evaluable ? "sin pérdida flotante" : "sin referencia"}</td>
                        <td className="small">{r.consistency ? (r.consistency.max_today !== null ? `hoy ${signedMoney(r.consistency.today)} / máx. ${usd0(r.consistency.max_today)}` : r.consistency.best_share !== null ? `mejor día ${r.consistency.best_share.toFixed(0)} %` : "—") : "—"}</td>
                        <td className="num">{r.contracts.open}{r.contracts.max ? ` / ${r.contracts.max}` : ""}</td>
                        <td className="small">{r.payout ? (r.payout.is_eval ? `evaluación${r.payout.status === "passed" ? " ✅" : ""}` : r.payout.eligible ? `✅ ${usd0(r.payout.available)}` : `faltan ${r.payout.missing.length}`) + (r.payout?.pending ? ` · pendiente ${usd0(r.payout.pending)}` : "") : "—"}</td>
                        <td>{ICON[r.status]} {r.alerts.length ? `${r.alerts.length} aviso${r.alerts.length > 1 ? "s" : ""}` : "ok"}</td>
                      </tr>
                      {open === r.account_id && (
                        <tr key={`${r.account_id}-d`} className="guardian-detail"><td colSpan={10}>
                          {r.alerts.length === 0 ? <span className="muted small">Sin avisos. Última operación: {r.last_trade_day ?? "nunca"}{r.inactive_days !== null ? ` (hace ${r.inactive_days} días)` : ""}.</span> : (
                            <ul className="alerts">
                              {r.alerts.map((a, i) => (
                                <li key={i} className={`alert ${a.level === "red" ? "bad" : "warn"}`}>
                                  <div className="alert-main"><b>{ICON[a.level]} {a.cause}</b>
                                    <span className="muted small">{a.value !== null ? `valor ${typeof a.value === "number" ? a.value.toLocaleString("es-ES") : a.value}` : ""}{a.limit !== null ? ` · límite ${a.limit.toLocaleString("es-ES")}` : ""}{a.margin !== null ? ` · margen ${a.margin.toLocaleString("es-ES")}` : ""} · {a.source}</span>
                                    <span className="small"><b>Acción:</b> {a.action}</span></div>
                                </li>
                              ))}
                            </ul>
                          )}
                          {r.payout && r.payout.missing.length > 0 && <div className="muted small">Para el retiro falta: {r.payout.missing.join("; ")}</div>}
                        </td></tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {err && <p className="bad small">{err}</p>}
        </>
      )}
    </Card>
  );
}
