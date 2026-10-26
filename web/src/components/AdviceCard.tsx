import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import type { Advice, AdviceAction, AdviceList } from "../lib/api";
import { Card, Empty } from "./ui";
import { Icon } from "./Icons";
import type { Route } from "./Shell";

/* Recomendaciones: lo que el engine aconseja hacer ahora con cada cuenta (desincronizada, drawdown alto, racha, sin
 * límites, multiplicador alto…) con el botón que lo resuelve. Se recalcula cada 30 s y tras cada acción. */
const TONE: Record<Advice["severity"], string> = { bad: "bad", warn: "warn", ok: "ok", info: "info" };

export function AdviceCard({ onRoute }: { onRoute?: (r: Route) => void }) {
  const { client, rules, health, refresh, setRisk, guard } = useStore();
  const [data, setData] = useState<AdviceList | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [hideInfo, setHideInfo] = useState(true);
  const master = health?.bridge.master_account ?? null;
  const load = () => client?.advice().then(setData).catch(() => {});
  useEffect(() => { if (!client) return; load(); const t = window.setInterval(load, 30000); return () => window.clearInterval(t); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (a: Advice, act: AdviceAction) => {
    if (!client) return;
    const key = a.account_id + act.type;
    setBusy(key); setMsg(null);
    try {
      const link = rules.find((r) => master && r.master_account.toLowerCase() === master.toLowerCase() && r.follower_account.toLowerCase() === a.account_id.toLowerCase() && !r.symbol_filter);
      if (act.type === "preset" && act.preset_id) { const r = await client.applyPreset(act.preset_id, [a.account_id]); setMsg(`${a.account_id}: preset ${r.preset.name} aplicado.`); setRisk(await client.risk()); }
      else if (act.type === "pause" && master) { await client.link(a.account_id, master, link?.multiplier ?? 1, false); setMsg(`${a.account_id}: copia pausada.`); }
      else if (act.type === "link" && master) { await client.link(a.account_id, master, 1, true); setMsg(`${a.account_id}: vinculada a ${master}.`); }
      else if (act.type === "multiplier" && master) { await client.link(a.account_id, master, act.value ?? 1, link?.enabled ?? true); setMsg(`${a.account_id}: multiplicador ×${act.value ?? 1}.`); }
      else if (act.type === "resync") { await client.resync(a.account_id); setMsg(`${a.account_id}: orden de igualar enviada.`); }
      else if (act.type === "flatten") { if (!(await guard())) return; if (!confirm(`¿Cerrar ${a.account_id}? Se cancelan sus órdenes y se cierra a mercado.`)) return; await client.flatten(a.account_id, "recomendación"); setMsg(`${a.account_id}: cierre enviado.`); }
      else if (act.type === "profile") { const r = await client.autoRisk(a.account_id, true); setMsg(r.applied.length ? `${a.account_id}: perfil ${r.applied[0].firm} ${r.applied[0].size / 1000}K aplicado (${r.applied[0].contracts} contratos).` : `${a.account_id}: no se pudo deducir el plan; ponlo en Cuentas → Prop firm.`); setRisk(await client.risk()); }
      else if (act.type === "resume") { onRoute?.("risk"); return; }
      await refresh(); await load();
    } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  };
  const list = (data?.advice ?? []).filter((a) => !hideInfo || a.severity !== "info");
  const n = data?.counts;
  return (
    <Card title={`Recomendaciones${n ? ` · ${n.bad + n.warn}` : ""}`} icon="shield" right={<label className="inline small"><input type="checkbox" checked={hideInfo} onChange={(e) => setHideInfo(e.target.checked)} /> ocultar informativas</label>} className={n?.bad ? "alerts-card has-bad" : ""}>
      {!data ? <p className="muted small">Calculando…</p> : list.length === 0 ? <Empty>Nada que recomendar ahora: límites puestos, sin desincronizaciones ni rachas.</Empty> : (
        <ul className="alerts" data-testid="advice-list">
          {list.map((a) => (
            <li key={a.account_id + a.code} className={`alert ${TONE[a.severity]}`} data-testid={`advice-${a.code}`}>
              <div className="alert-main">
                <b>{a.account_id} · {a.title}</b>
                <span className="muted">{a.detail}</span>
              </div>
              <div className="chips">
                {a.actions.map((act) => <button key={act.type + (act.preset_id ?? "")} className={`chip-btn ${act.type === "flatten" ? "danger" : ""}`} disabled={busy !== null} onClick={() => void run(a, act)}>{busy === a.account_id + act.type ? "…" : act.label}</button>)}
                <button className="chip-btn" onClick={() => onRoute?.(a.code === "NO_RULE" || a.code === "DESYNC" ? "accounts" : "risk")}><Icon name="activity" size={12} /></button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {msg && <p className="small" data-testid="advice-msg">{msg}</p>}
    </Card>
  );
}
