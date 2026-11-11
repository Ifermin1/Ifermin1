import { useState } from "react";
import { useStore } from "../lib/store";
import type { Account, RiskLimit } from "../lib/api";
import { money, signedMoney } from "../lib/format";
import { Card, Empty } from "./ui";
import { DrawdownMeter, liveDrawdown, livePnl } from "./AccountPanel";
import { Icon } from "./Icons";

/* Riesgo por cuenta ACTIVA: una fila por cuenta que está en juego (habilitada, conectada y con saldo), con sus límites
 * editables en la misma fila. Las cuentas que el bróker lista en 0,00, las desconectadas y las ocultas no aparecen: el
 * engine tampoco las vigila. Guardar escribe el RiskLimit completo (mismo valor que en Cuentas → ▾ y que el engine usa). */
export const isActive = (a: Account) => a.enabled && a.connected !== false && (a.balance || 0) > 0;

type Draft = { max_daily_loss: string; max_daily_profit: string; profit_goal: string; start_balance: string; max_position_size: string; max_trailing_drawdown: string; drawdown_buffer: string; drawdown_mode: RiskLimit["drawdown_mode"] };
const EMPTY: Omit<RiskLimit, "account_id"> = { max_daily_loss: 0, max_daily_profit: 0, profit_goal: 0, start_balance: 0, max_position_size: 0, max_trailing_drawdown: 0, drawdown_mode: "intraday", drawdown_floor_cap: 0, drawdown_buffer: 0, trading_halted: false, halted_reason: "", halted_at: null };
const toDraft = (l: Omit<RiskLimit, "account_id">): Draft => ({ max_daily_loss: String(l.max_daily_loss || ""), max_daily_profit: String(l.max_daily_profit || ""), profit_goal: String(l.profit_goal || ""), start_balance: String(l.start_balance || ""),
  max_position_size: String(l.max_position_size || ""), max_trailing_drawdown: String(l.max_trailing_drawdown || ""), drawdown_buffer: String(l.drawdown_buffer || ""), drawdown_mode: l.drawdown_mode });
const num = (s: string) => Math.max(0, Number(String(s).replace(",", ".")) || 0);
const MODE: Record<RiskLimit["drawdown_mode"], string> = { intraday: "dinámico", eod: "EOD", closed: "cerrado", static: "estático" };
const REASON: Record<string, string> = { daily_loss: "stop diario", daily_profit: "corte del día", profit_goal: "objetivo", drawdown: "drawdown", consistency: "consistencia", tilt: "tilt", manual: "a mano" };

export function ActiveRiskTable({ onlyCopying = false }: { onlyCopying?: boolean }) {
  const { client, accounts, rules, risk, setRisk, health, prices, guard, readOnly, refresh } = useStore();
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(!onlyCopying);
  const master = (health?.bridge.master_account ?? "").toLowerCase();
  const limits = new Map((risk?.limits ?? []).map((l) => [l.account_id, l]));
  const copying = (a: Account) => a.account_id.toLowerCase() === master || rules.some((r) => r.enabled && !r.symbol_filter && r.master_account.toLowerCase() === master && r.follower_account.toLowerCase() === a.account_id.toLowerCase());
  const active = accounts.filter(isActive).filter((a) => showAll || copying(a))
    .sort((x, y) => (x.account_id.toLowerCase() === master ? -1 : y.account_id.toLowerCase() === master ? 1 : 0) || Number(copying(y)) - Number(copying(x)) || (x.alias || x.account_id).localeCompare(y.alias || y.account_id));
  const inactive = accounts.filter((a) => !isActive(a)).length;
  if (!client) return null;

  // solo hay borrador para la fila que se está editando; el resto enseña siempre lo que tiene el engine
  const dirty = (a: Account) => { const l = limits.get(a.account_id) ?? EMPTY; const d = drafts[a.account_id]; return !!d && JSON.stringify(d) !== JSON.stringify(toDraft(l)); };
  const set = (id: string, patch: Partial<Draft>) => setDrafts((d) => ({ ...d, [id]: { ...(d[id] ?? toDraft(limits.get(id) ?? EMPTY)), ...patch } }));
  const discard = (id: string) => setDrafts((x) => { const n = { ...x }; delete n[id]; return n; });
  const run = async (id: string, fn: () => Promise<unknown>, ok: string) => {
    if (!(await guard())) return;
    setBusy(id); setErr(null); setMsg(null);
    try { await fn(); setRisk(await client.risk()); await refresh(); setMsg(ok); } catch (ex) { setErr(`${id}: ${ex instanceof Error ? ex.message : String(ex)}`); } finally { setBusy(null); }
  };
  const save = (a: Account, halted?: boolean) => {
    const l = limits.get(a.account_id) ?? EMPTY; const d = drafts[a.account_id] ?? toDraft(l);
    const goal = num(d.profit_goal);
    const start = num(d.start_balance) || (goal > 0 ? Math.round(a.balance) : 0);     // objetivo sin saldo inicial: desde el saldo de ahora
    return run(a.account_id, () => client.upsertLimit({ account_id: a.account_id, max_daily_loss: num(d.max_daily_loss), max_daily_profit: num(d.max_daily_profit), profit_goal: goal, start_balance: start,
      max_position_size: Math.round(num(d.max_position_size)), max_trailing_drawdown: num(d.max_trailing_drawdown), drawdown_mode: d.drawdown_mode, drawdown_floor_cap: l.drawdown_floor_cap, drawdown_buffer: num(d.drawdown_buffer),
      trading_halted: halted ?? l.trading_halted }).then(() => setDrafts((x) => { const n = { ...x }; delete n[a.account_id]; return n; })),
      halted === true ? `${a.alias || a.account_id} pausada.` : halted === false ? `${a.alias || a.account_id} reanudada.` : `Límites de ${a.alias || a.account_id} guardados.`);
  };
  const clear = (a: Account) => { if (confirm(`¿Quitar todos los límites de ${a.alias || a.account_id}? Copiará sin stop diario, sin tope y sin drawdown vigilado.`)) void run(a.account_id, () => client.deleteLimit(a.account_id).then(() => setDrafts((x) => { const n = { ...x }; delete n[a.account_id]; return n; })), `Límites de ${a.alias || a.account_id} quitados.`); };

  return (
    <Card title={`Riesgo por cuenta activa · ${active.length}`} icon="shield"
          right={<span className="chips">
            {onlyCopying && <button className={`chip-btn ${showAll ? "active" : ""}`} onClick={() => setShowAll(!showAll)} data-testid="risk-show-all">{showAll ? "Solo las que copian" : "También las que no copian"}</button>}
            {inactive > 0 && <span className="chip" title="Ocultas, desconectadas o con saldo 0,00: el engine no las vigila ni les aplica perfil. En Cuentas → Todas puedes verlas.">{inactive} inactivas fuera</span>}
          </span>}>
      <p className="muted small" style={{ margin: "0 0 8px" }}>Solo las cuentas <b>en juego</b> (habilitadas, conectadas y con saldo). Cambia un valor y pulsa <b>Guardar</b> en su fila; 0 o vacío = sin límite. El stop diario y el corte son netos del día; el objetivo es acumulado sobre el saldo inicial; el drawdown es el del prop firm (suelo = máximo − drawdown).</p>
      {active.length === 0 ? <Empty>{accounts.length ? "Ninguna cuenta activa ahora mismo (conectadas con saldo)." : "Esperando cuentas del bróker…"}</Empty> : (
        <div className="table-wrap"><table className="active-risk" data-testid="active-risk">
          <thead><tr><th>Cuenta · drawdown</th><th className="num">Hoy (neto)</th><th className="num" title="Pérdida neta del día a la que se pausa y cierra">Stop diario $</th><th className="num" title="Ganancia neta del día a la que se cierra y asegura">Corte día $</th>
            <th className="num" title="Ganancia acumulada sobre el saldo inicial (evaluación)">Objetivo $</th><th className="num" title="Saldo desde el que cuenta el objetivo (vacío = saldo de ahora al guardar)">Desde $</th><th className="num">Máx. contr.</th><th className="num" title="Drawdown máximo del prop firm y colchón al que el engine cierra antes">DD máx. $ · colchón</th><th>Estado</th><th></th></tr></thead>
          <tbody>{active.map((a) => {
            const l = limits.get(a.account_id); const d = drafts[a.account_id] ?? toDraft(l ?? EMPTY);
            const isM = a.account_id.toLowerCase() === master; const pnl = livePnl(a, prices); const net = pnl.value - (a.commissions_today || 0); const dd = liveDrawdown(a, prices);
            const id = a.account_id; const b = busy === id; const dirtyRow = dirty(a);
            return (
              <tr key={id} className={`${l?.trading_halted ? "halted" : ""} ${dirtyRow ? "dirty" : ""}`} data-testid={`ar-${id}`}>
                <td><b>{isM && <Icon name="crown" size={11} />} {a.alias || id}</b>{a.alias && <span className="muted small"> {id}</span>}<div className="muted small">{isM ? "líder" : copying(a) ? "copiando" : "sin copiar"}{a.owner ? ` · ${a.owner}` : ""}{a.firm ? ` · ${a.firm.toUpperCase()}` : ""}</div>
                  <div className="meter-cell">{dd.pct !== null ? <DrawdownMeter d={dd} /> : <span className="muted small">drawdown sin límite</span>}</div></td>
                <td className={`num ${net > 0 ? "ok" : net < 0 ? "bad" : "muted"}`}>{pnl.live && "≈ "}{signedMoney(net)}<div className="muted small">{money(a.balance)}</div></td>
                <td className="num"><input inputMode="decimal" value={d.max_daily_loss} onChange={(e) => set(id, { max_daily_loss: e.target.value })} disabled={readOnly || b} data-testid={`ar-loss-${id}`} /></td>
                <td className="num"><input inputMode="decimal" value={d.max_daily_profit} onChange={(e) => set(id, { max_daily_profit: e.target.value })} disabled={readOnly || b} data-testid={`ar-cut-${id}`} /></td>
                <td className="num"><input inputMode="decimal" value={d.profit_goal} onChange={(e) => set(id, { profit_goal: e.target.value })} disabled={readOnly || b} data-testid={`ar-goal-${id}`} /></td>
                <td className="num"><input inputMode="decimal" value={d.start_balance} placeholder={d.profit_goal ? String(Math.round(a.balance)) : ""} onChange={(e) => set(id, { start_balance: e.target.value })} disabled={readOnly || b} /></td>
                <td className="num"><input inputMode="numeric" value={d.max_position_size} onChange={(e) => set(id, { max_position_size: e.target.value.replace(/[^0-9]/g, "") })} disabled={readOnly || b} data-testid={`ar-cap-${id}`} /></td>
                <td className="num"><span className="dd-edit"><input inputMode="decimal" value={d.max_trailing_drawdown} onChange={(e) => set(id, { max_trailing_drawdown: e.target.value })} disabled={readOnly || b} />
                  <select value={d.drawdown_mode} onChange={(e) => set(id, { drawdown_mode: e.target.value as RiskLimit["drawdown_mode"] })} disabled={readOnly || b} title="Cómo mide el prop firm el máximo">{Object.entries(MODE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                  <input inputMode="decimal" value={d.drawdown_buffer} placeholder="colch." onChange={(e) => set(id, { drawdown_buffer: e.target.value })} disabled={readOnly || b} title="Colchón: el engine pausa y cierra cuando quedan estos $ hasta el suelo" /></span></td>
                <td>{l?.trading_halted ? <span className={`badge ${l.halted_reason === "daily_profit" || l.halted_reason === "profit_goal" ? "ok" : "bad"}`}>pausada · {REASON[l.halted_reason] ?? (l.halted_reason || "a mano")}</span> : l ? <span className="badge ok">vigilada</span> : <span className="badge muted">sin límites</span>}</td>
                <td className="row-actions">
                  {!readOnly && (dirtyRow || !l) && <button className="primary small-btn" disabled={b} onClick={() => void save(a)} data-testid={`ar-save-${id}`}>Guardar</button>}
                  {!readOnly && dirtyRow && <button className="ghost small-btn" disabled={b} onClick={() => discard(id)} title="Descartar los cambios de esta fila">↶</button>}
                  {!readOnly && l && !dirtyRow && (l.trading_halted
                    ? <button className="ghost small-btn" disabled={b} onClick={() => void save(a, false)} data-testid={`ar-resume-${id}`}>Reanudar</button>
                    : <button className="ghost small-btn" disabled={b} onClick={() => { if (confirm(`¿Pausar ${a.alias || id}? No recibirá copias hasta reanudarla.`)) void save(a, true); }} data-testid={`ar-pause-${id}`}>Pausar</button>)}
                  {!readOnly && l && !dirtyRow && <button className="ghost small-btn danger" disabled={b} onClick={() => clear(a)} title="Quitar todos los límites">✕</button>}
                </td>
              </tr>
            );
          })}</tbody>
        </table></div>
      )}
      {err && <p className="error small" data-testid="ar-error">{err}</p>}
      {msg && !err && <p className="ok small" data-testid="ar-msg">{msg}</p>}
    </Card>
  );
}
