import { useEffect, useState } from "react";
import type { Client, PropRuleStatus } from "../lib/api";
import { useStore } from "../lib/store";
import { Card, Empty } from "./ui";
import { signedMoney } from "../lib/format";

/* Reglas de prop firm: cuánto falta para el payout por cuenta y margen de hoy para no romper la consistencia. */
const usd0 = (n: number) => `${Math.round(n).toLocaleString("es-ES")} $`;

export function PropRulesCard({ client }: { client: Client }) {
  const { readOnly } = useStore();
  const [rows, setRows] = useState<PropRuleStatus[] | null>(null);
  const [edit, setEdit] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => client.propRules().then((r) => setRows(r.accounts)).catch(() => {});
  useEffect(() => { void load(); const t = window.setInterval(load, 60000); return () => window.clearInterval(t); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  const save = async (acc: string, k: string, v: number | boolean) => { try { const r = await client.propRuleSet(acc, { [k]: v }); setRows((rs) => (rs ?? []).map((x) => (x.account_id === acc ? r : x))); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } };
  return (
    <Card title="Reglas de prop firm y payout" right={<span className="muted small">cifras orientativas por firma: corrígelas por cuenta si tu plan es distinto</span>}>
      {!rows ? <p className="muted small">Cargando…</p> : rows.length === 0 ? <Empty>Sin cuentas con firma: pon el prop firm en Cuentas (o aplica el perfil automático en Riesgo).</Empty> : (
        <div className="prop-grid" data-testid="prop-rules">
          {rows.map((r) => (
            <div key={r.account_id} className={`prop-acc ${r.eligible ? "ok" : ""}`} data-testid={`prop-${r.account_id}`}>
              <div className="prop-head">
                <b>{r.alias || r.account_id}</b> <span className="muted small">{r.firm} · {r.plan}{r.start_balance ? ` · ${usd0(r.start_balance)}` : ""}</span>
                <span className={`badge ${r.is_eval ? "" : r.eligible ? "ok" : "warn"}`}>{r.is_eval ? "evaluación" : r.eligible ? `payout disponible · ${usd0(r.available)}` : "aún no elegible"}</span>
              </div>
              <div className="muted small">ganancia {signedMoney(r.profit)} · hoy {signedMoney(r.today)} · {r.traded_days} días operados · mejor día {signedMoney(r.best_day)}{r.best_day_share !== null ? ` (${r.best_day_share} %)` : ""}{r.n_payouts ? ` · retiros: ${r.n_payouts} (${usd0(r.payouts_paid)} cobrados)` : ""}</div>
              {r.max_today !== null && !r.is_eval && <div className={`small ${r.today_room !== null && r.today_room < 0 ? "bad" : r.today_room !== null && r.today_room < 0.2 * r.max_today ? "warn" : ""}`}>Hoy puedes ganar hasta <b>{usd0(r.max_today)}</b> sin romper la consistencia{r.today_room !== null ? (r.today_room < 0 ? ` · ya superado en ${usd0(-r.today_room)}` : ` · quedan ${usd0(r.today_room)}`) : ""}.</div>}
              <ul className="prop-reqs">
                {r.requirements.map((q) => <li key={q.key} className={q.status}><span className="prop-ico">{q.status === "ok" ? "✔" : "○"}</span><b>{q.label}</b> <span className="muted">{q.detail}</span>{q.status !== "ok" && q.missing ? <span className="warn"> · faltan {q.key === "days" || q.key === "winning_days" ? `${q.missing} días` : usd0(q.missing)}</span> : null}</li>)}
              </ul>
              <button className="link tiny" onClick={() => setEdit(edit === r.account_id ? null : r.account_id)}>{edit === r.account_id ? "cerrar ajustes" : "ajustar reglas de esta cuenta"}</button>
              {edit === r.account_id && (
                <div className="chips small" style={{ marginTop: 6 }}>
                  <label className="inline small">consistencia % <input type="number" min={5} max={100} value={Math.round(r.rule.consistency * 100)} disabled={readOnly} onChange={(e) => void save(r.account_id, "consistency", Number(e.target.value) / 100)} style={{ width: 60 }} /></label>
                  <label className="inline small">días mín. <input type="number" min={0} max={60} value={r.rule.min_days} disabled={readOnly} onChange={(e) => void save(r.account_id, "min_days", Number(e.target.value))} style={{ width: 56 }} /></label>
                  <label className="inline small">días ganadores <input type="number" min={0} max={60} value={r.rule.winning_days} disabled={readOnly} onChange={(e) => void save(r.account_id, "winning_days", Number(e.target.value))} style={{ width: 56 }} /></label>
                  <label className="inline small">día ganador ≥ $ <input type="number" min={0} value={r.rule.min_day_profit} disabled={readOnly} onChange={(e) => void save(r.account_id, "min_day_profit", Number(e.target.value))} style={{ width: 70 }} /></label>
                  <label className="inline small"><input type="checkbox" checked={r.rule.safety_net} disabled={readOnly} onChange={(e) => void save(r.account_id, "safety_net", e.target.checked)} /> colchón (dd + <input type="number" min={0} value={r.rule.safety_extra} disabled={readOnly} onChange={(e) => void save(r.account_id, "safety_extra", Number(e.target.value))} style={{ width: 60 }} /> $)</label>
                  <label className="inline small">retiro mín. $ <input type="number" min={0} value={r.rule.payout_min} disabled={readOnly} onChange={(e) => void save(r.account_id, "payout_min", Number(e.target.value))} style={{ width: 70 }} /></label>
                  <label className="inline small">retiro máx. $ <input type="number" min={0} value={r.rule.payout_max} disabled={readOnly} onChange={(e) => void save(r.account_id, "payout_max", Number(e.target.value))} style={{ width: 70 }} /></label>
                  <label className="inline small">% retirable <input type="number" min={1} max={100} value={r.rule.payout_max_pct} disabled={readOnly} onChange={(e) => void save(r.account_id, "payout_max_pct", Number(e.target.value))} style={{ width: 56 }} /></label>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {msg && <p className="muted small">{msg}</p>}
    </Card>
  );
}
