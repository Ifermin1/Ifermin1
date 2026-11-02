import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import type { Account, PropRuleStatus, RiskLimit } from "../lib/api";
import { signedMoney, money } from "../lib/format";

/* Modo evaluación de una cuenta: las reglas de la prueba (días, consistencia, mantenimiento), el estado frente a cada
 * una y el gestor automático que para por consistencia, baja el tamaño cuando el objetivo llega antes que los días y
 * cierra la cuenta cuando está superada. El objetivo, la pérdida diaria y el drawdown son los de Riesgo (botón Prop firm). */
const STATUS: Record<string, { label: string; tone: string }> = {
  in_progress: { label: "en curso", tone: "" }, target_wait: { label: "objetivo conseguido · faltan reglas", tone: "warn" },
  passed: { label: "✅ superada", tone: "ok" }, consistency_stop: { label: "parada hoy por consistencia", tone: "warn" },
};

export function EvalManager({ a, limit }: { a: Account; limit?: RiskLimit }) {
  const { client, readOnly, guard } = useStore();
  const [st, setSt] = useState<PropRuleStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ min_days: "", min_day_profit: "", consistency: "", maint_contracts: "" });
  const load = () => client?.evalGet(a.account_id).then((r) => { setSt(r); setForm({ min_days: String(r.eval_rule.min_days), min_day_profit: String(r.eval_rule.min_day_profit), consistency: String(Math.round(r.eval_rule.consistency * 100)), maint_contracts: String(r.eval_rule.maint_contracts) }); }).catch(() => {});
  useEffect(() => { void load(); const t = window.setInterval(load, 30000); return () => window.clearInterval(t); }, [client, a.account_id, limit?.profit_goal, limit?.trading_halted]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!client || !st) return null;
  const ev = st.eval_rule;
  const save = async (changes: Partial<{ auto: boolean; min_days: number; min_day_profit: number; consistency: number; maint_contracts: number }>) => {
    if (changes.auto !== undefined && !(await guard())) return;
    setBusy(true); setErr(null);
    try { setSt(await client.evalSet(a.account_id, changes)); } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(false); }
  };
  const saveForm = () => void save({ min_days: Number(form.min_days) || 0, min_day_profit: Number(form.min_day_profit.replace(",", ".")) || 0, consistency: (Number(form.consistency.replace(",", ".")) || 0) / 100, maint_contracts: Number(form.maint_contracts) || 0 });
  const s = STATUS[st.status ?? "in_progress"] ?? STATUS.in_progress;
  const days = st.requirements.find((r) => r.key === "days");
  const cons = st.requirements.find((r) => r.key === "consistency");
  return (
    <div className={`eval-box ${ev.auto ? "on" : ""}`} data-testid={`eval-${a.account_id}`}>
      <div className="eval-head">
        <label className="switch small" title="Gestor automático de la evaluación: para por consistencia, baja el tamaño si el objetivo llega antes que los días y cierra la cuenta al superarla">
          <input type="checkbox" checked={ev.auto} disabled={busy || readOnly} onChange={(e) => void save({ auto: e.target.checked })} data-testid={`eval-auto-${a.account_id}`} /><span />
        </label>
        <b>Modo evaluación</b>
        <span className="muted small">{a.firm ? `${a.firm} · ${a.plan || "plan"}` : "sin prop firm (ponlo con el botón Prop firm)"}{st.goal > 0 ? ` · objetivo ${money(st.goal)}` : " · sin objetivo (ponlo en Objetivo evaluación)"}</span>
        <span className={`badge ${s.tone}`} data-testid={`eval-status-${a.account_id}`}>{ev.auto ? `gestor ON · ${s.label}` : "gestor OFF"}</span>
        <button className="link tiny" onClick={() => setOpen(!open)}>{open ? "ocultar reglas" : "reglas de la prueba"}</button>
      </div>
      <div className="eval-line small" data-testid={`eval-line-${a.account_id}`}>
        {st.goal > 0 && <span>objetivo <b className={st.profit >= st.goal ? "ok" : ""}>{signedMoney(st.profit)}</b> / {money(st.goal)} ({Math.min(999, Math.round(100 * st.profit / st.goal))} %)</span>}
        {days && <span> · días <b className={days.status === "ok" ? "ok" : ""}>{days.detail}</b></span>}
        {cons && <span> · consistencia <b className={cons.status === "ok" ? "ok" : "warn"}>{cons.detail}</b></span>}
        {st.max_today !== null && st.max_today > 0 && <span> · hoy {signedMoney(st.today)}, máximo hoy <b className={st.today_room !== null && st.today_room < 0 ? "bad" : ""}>{money(st.max_today)}</b>{st.today_room !== null && st.today_room >= 0 ? ` (quedan ${money(st.today_room)})` : " (superado)"}</span>}
        {ev.auto && st.status === "target_wait" && <span className="warn"> · tamaño en mantenimiento: {ev.maint_contracts} contr.</span>}
      </div>
      {open && (
        <div className="eval-form" data-testid={`eval-form-${a.account_id}`}>
          <p className="muted small" style={{ margin: 0 }}>Cifras orientativas por firma: pon las de TU plan. Lo que hace el gestor: <b>consistencia</b> → cierra y para la cuenta hasta mañana cuando lo ganado hoy llega al máximo que permite la regla; <b>objetivo antes que los días</b> → no la cierra, baja el tope a los contratos de mantenimiento y avisa; <b>todo cumplido</b> → cierra y pausa la cuenta como superada. Pérdida diaria y drawdown los vigila Riesgo como siempre.</p>
          <div className="rule-form">
            <label>Días mínimos operados<input inputMode="numeric" value={form.min_days} onChange={(e) => setForm({ ...form, min_days: e.target.value })} data-testid={`eval-days-${a.account_id}`} /></label>
            <label>Ganancia mínima para que un día cuente ($, 0 = cualquiera)<input inputMode="decimal" value={form.min_day_profit} onChange={(e) => setForm({ ...form, min_day_profit: e.target.value })} /></label>
            <label>Consistencia: mejor día ≤ % del total (0 = sin regla)<input inputMode="decimal" value={form.consistency} onChange={(e) => setForm({ ...form, consistency: e.target.value })} data-testid={`eval-cons-${a.account_id}`} /></label>
            <label>Contratos en mantenimiento (objetivo conseguido, faltan días)<input inputMode="numeric" value={form.maint_contracts} onChange={(e) => setForm({ ...form, maint_contracts: e.target.value })} /></label>
            <button className="primary" disabled={busy || readOnly} onClick={saveForm} data-testid={`eval-save-${a.account_id}`}>{busy ? "…" : "Guardar reglas"}</button>
          </div>
          <ul className="prop-reqs">
            {st.requirements.map((r) => <li key={r.key} className={r.status}>{r.status === "ok" ? "✓" : "○"} {r.label}: {r.detail}{r.missing ? ` · faltan ${typeof r.missing === "number" && r.key !== "days" ? money(r.missing) : r.missing}` : ""}</li>)}
          </ul>
        </div>
      )}
      {err && <p className="bad small">{err}</p>}
    </div>
  );
}
