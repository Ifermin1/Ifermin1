import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import type { DynStopState } from "../lib/api";
import { Card } from "./ui";
import { money } from "../lib/format";

/* Stop dinámico por cuenta: cada cuenta solo puede perder lo que le queda de SU ventana (stop diario / drawdown) menos un
 * margen; si el stop copiado pierde más, se acerca solo en esa cuenta. La maestra queda fuera salvo que se incluya. */
export function DynStopCard() {
  const { client, readOnly, guard, accounts } = useStore();
  const [st, setSt] = useState<DynStopState | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = () => client?.dynstop().then(setSt).catch(() => {});
  useEffect(() => { void load(); const t = window.setInterval(load, 10000); return () => window.clearInterval(t); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!client || !st) return null;
  const save = async (changes: Record<string, number | boolean>) => {
    if (changes.enabled !== undefined && !(await guard())) return;
    setBusy(true); setErr(null);
    try { setSt(await client.dynstopSet(changes)); } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(false); }
  };
  const c = st.config;
  const rows = Object.entries(st.accounts).sort(([a], [b]) => a.localeCompare(b));
  const alias = (id: string) => accounts.find((a) => a.account_id === id)?.alias || id;
  return (
    <Card title={`Stop dinámico por cuenta · ${c.enabled ? "ON" : "OFF"} · ${st.stats.moves} ajustes`} icon="target"
          right={<label className="switch small" title="Acercar el stop de cada cuenta a lo que le queda de su ventana"><input type="checkbox" checked={c.enabled} disabled={busy || readOnly} onChange={(e) => void save({ enabled: e.target.checked })} data-testid="dynstop-enabled" /><span /></label>}>
      <p className="muted small" style={{ margin: 0 }}>Cada cuenta solo puede perder lo que le queda de <b>su</b> ventana: la menor entre el stop loss diario que le queda y el margen de drawdown (menos el colchón), menos el margen de seguridad. Si el stop copiado de la maestra perdería más, el engine <b>acerca el stop solo en esa cuenta</b> (nunca lo aleja, nunca toca a las demás). Si la ventana ya no da ni para {c.min_ticks} ticks, lo deja como está y avisa: el límite de riesgo cerrará la cuenta si llega.</p>
      <div className="rule-form guardian-cfg" data-testid="dynstop-cfg">
        <label>Margen de seguridad ($ por debajo del límite)<input inputMode="decimal" defaultValue={c.margin_usd} onBlur={(e) => void save({ margin_usd: Number(e.target.value.replace(",", ".")) || 0 })} data-testid="dynstop-margin" /></label>
        <label>Distancia mínima al precio (ticks)<input inputMode="numeric" defaultValue={c.min_ticks} onBlur={(e) => void save({ min_ticks: Number(e.target.value) || 4 })} /></label>
        <label className="inline"><input type="checkbox" checked={c.use_drawdown} disabled={busy || readOnly} onChange={(e) => void save({ use_drawdown: e.target.checked })} /> también el margen de drawdown</label>
        <label className="inline"><input type="checkbox" checked={c.create_if_missing} disabled={busy || readOnly} onChange={(e) => void save({ create_if_missing: e.target.checked })} /> crear el stop si la cuenta no tiene</label>
        <label className="inline"><input type="checkbox" checked={c.include_master} disabled={busy || readOnly} onChange={(e) => void save({ include_master: e.target.checked })} /> también la maestra</label>
      </div>
      {rows.length > 0 && (
        <table className="guardian-table" data-testid="dynstop-table">
          <thead><tr><th>Cuenta</th><th className="num">Ventana</th><th>Limitada por</th><th className="num">Stop</th><th className="num">Pérdida al stop</th><th>Última acción</th></tr></thead>
          <tbody>
            {rows.map(([id, r]) => (
              <tr key={id}><td><b>{alias(id)}</b>{r.symbol ? <span className="muted small"> · {r.symbol}</span> : null}</td>
                <td className="num">{r.room === null ? "—" : money(r.room)}</td><td className="small">{r.limited_by ?? "—"}</td>
                <td className="num">{r.stop ?? "—"}</td><td className="num">{r.loss_at_stop === null || r.loss_at_stop === undefined ? "—" : money(r.loss_at_stop)}</td>
                <td className="small muted">{r.note} · {r.at.slice(11, 19)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {err && <p className="bad small">{err}</p>}
    </Card>
  );
}
