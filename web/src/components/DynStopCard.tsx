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
    <Card title={`Stop y corte dinámicos por cuenta · ${c.enabled ? "ON" : "OFF"} · ${st.stats.moves} stops · ${st.stats.cuts} objetivos`} icon="target"
          right={<label className="switch small" title="Acercar el stop de cada cuenta a lo que le queda de su ventana"><input type="checkbox" checked={c.enabled} disabled={busy || readOnly} onChange={(e) => void save({ enabled: e.target.checked })} data-testid="dynstop-enabled" /><span /></label>}>
      <p className="muted small" style={{ margin: 0 }}>Cada cuenta solo puede perder lo que le queda de <b>su</b> ventana: la menor entre el stop loss diario que le queda y el margen de drawdown (menos el colchón), menos el margen de seguridad. Si el stop copiado de la maestra perdería más, el engine <b>acerca el stop solo en esa cuenta</b> (nunca lo aleja, nunca toca a las demás). Si la ventana ya no da ni para {c.min_ticks} ticks, lo deja como está y avisa: el límite de riesgo cerrará la cuenta si llega. <b>Corte del día con límite:</b> si la cuenta tiene ganancia máxima del día, el objetivo (orden límite) se coloca justo donde la ganancia neta del día llega al corte, contando la comisión de cerrar: ganas exactamente lo que marcaste, con una orden al precio y no a mercado después.</p>
      <div className="rule-form guardian-cfg" data-testid="dynstop-cfg">
        <label>Margen de seguridad ($ por debajo del límite)<input inputMode="decimal" defaultValue={c.margin_usd} onBlur={(e) => void save({ margin_usd: Number(e.target.value.replace(",", ".")) || 0 })} data-testid="dynstop-margin" /></label>
        <label>Distancia mínima al precio (ticks)<input inputMode="numeric" defaultValue={c.min_ticks} onBlur={(e) => void save({ min_ticks: Number(e.target.value) || 4 })} /></label>
        <label className="inline"><input type="checkbox" checked={c.use_drawdown} disabled={busy || readOnly} onChange={(e) => void save({ use_drawdown: e.target.checked })} /> también el margen de drawdown</label>
        <label className="inline" title="Apagado por defecto desde el 11/11: un stop creado a ciegas en una cuenta operada a mano se ejecutó al instante. Si lo enciendes, espera la gracia tras la entrada antes de crearlo."><input type="checkbox" checked={c.create_if_missing} disabled={busy || readOnly} onChange={(e) => void save({ create_if_missing: e.target.checked })} data-testid="dynstop-create" /> crear el stop si la cuenta no tiene (solo mueve el que exista si está apagado)</label>
        {c.create_if_missing && <label>Gracia antes de crear (segundos tras la entrada)<input inputMode="numeric" defaultValue={c.create_grace_seconds} onBlur={(e) => void save({ create_grace_seconds: Math.max(0, Number(e.target.value) || 0) })} /></label>}
        <label className="inline"><input type="checkbox" checked={c.include_master} disabled={busy || readOnly} onChange={(e) => void save({ include_master: e.target.checked })} /> también la maestra</label>
        <label className="inline"><input type="checkbox" checked={c.profit_cut} disabled={busy || readOnly} onChange={(e) => void save({ profit_cut: e.target.checked })} data-testid="dynstop-cut" /> corte del día con orden límite</label>
        <label>Ajuste del corte ($, + por encima / − por debajo)<input inputMode="decimal" defaultValue={c.profit_margin_usd} onBlur={(e) => void save({ profit_margin_usd: Number(e.target.value.replace(",", ".")) || 0 })} /></label>
      </div>
      {rows.length > 0 && (
        <table className="guardian-table" data-testid="dynstop-table">
          <thead><tr><th>Cuenta</th><th className="num">Ventana</th><th>Limitada por</th><th className="num">Stop</th><th className="num">Pérdida al stop</th><th className="num">Objetivo (corte)</th><th>Última acción</th></tr></thead>
          <tbody>
            {rows.map(([id, r]) => (
              <tr key={id}><td><b>{alias(id)}</b>{r.symbol ? <span className="muted small"> · {r.symbol}</span> : null}</td>
                <td className="num">{r.room === null ? "—" : money(r.room)}</td><td className="small">{r.limited_by ?? "—"}</td>
                <td className="num">{r.stop ?? "—"}</td><td className="num">{r.loss_at_stop === null || r.loss_at_stop === undefined ? "—" : money(r.loss_at_stop)}</td>
                <td className="num" title={r.cut?.note}>{r.cut?.target ?? "—"}{r.cut?.need !== undefined ? <span className="muted small"> (+{money(r.cut.need)})</span> : null}</td>
                <td className="small muted">{[r.note, r.cut?.note].filter(Boolean).join(" · ")} · {r.at.slice(11, 19)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {err && <p className="bad small">{err}</p>}
    </Card>
  );
}
