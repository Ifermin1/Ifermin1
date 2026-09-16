import { useEffect, useState, type FormEvent } from "react";
import { useStore } from "../lib/store";
import { money, time, signedMoney } from "../lib/format";
import { Card, Empty } from "../components/ui";
import type { RiskLimit } from "../lib/api";
import { DrawdownMeter, ddTone } from "../components/AccountPanel";

export function Risk() {
  const { client, risk, setRisk, accounts } = useStore();
  const [account, setAccount] = useState("");
  const [maxLoss, setMaxLoss] = useState("0");
  const [maxProfit, setMaxProfit] = useState("0");
  const [maxSize, setMaxSize] = useState("0");
  const [maxDd, setMaxDd] = useState("0");
  const [ddMode, setDdMode] = useState<"intraday" | "closed">("intraday");
  const [ddCap, setDdCap] = useState("0");
  const [ddBuffer, setDdBuffer] = useState("0");
  const [halted, setHalted] = useState(false);
  if (!client) return null;

  const [sched, setSched] = useState({ enabled: false, window_start: "", flatten_at: "", include_master: true });
  useEffect(() => { if (risk?.schedule) setSched({ enabled: risk.schedule.enabled, window_start: risk.schedule.window_start, flatten_at: risk.schedule.flatten_at, include_master: risk.schedule.include_master }); }, [risk?.schedule]);
  const [flattenOnKill, setFlattenOnKill] = useState(true);
  const [flattenMaster, setFlattenMaster] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function toggleKill() {
    const active = !risk?.kill_switch;
    if (active && !confirm(flattenOnKill
      ? `¿KILL SWITCH? Se bloquean todas las réplicas Y SE CIERRAN a mercado las posiciones de las seguidoras${flattenMaster ? " y de la maestra" : ""}.`
      : "¿KILL SWITCH? Se bloquean todas las réplicas (las posiciones abiertas se quedan).")) return;
    const reason = active ? prompt("Motivo (opcional)") ?? undefined : undefined;
    setBusy(true);
    try { setRisk(await client!.killSwitch(active, reason, active && flattenOnKill, flattenMaster)); } finally { setBusy(false); }
  }
  async function flattenAll(includeMaster: boolean) {
    if (!confirm(includeMaster ? "¿CERRAR TODO, incluida la maestra? Cancela órdenes y cierra posiciones a mercado en todas las cuentas."
                               : "¿Cerrar todas las seguidoras? Cancela sus órdenes y cierra sus posiciones a mercado.")) return;
    setBusy(true); setMsg(null);
    try {
      const r = await client!.flattenAll(includeMaster, "manual desde consola");
      const entries = Object.entries(r.results);
      setMsg(entries.length ? entries.map(([a, res]) => `${a}: ${res.startsWith("OK") ? "cerrada" : res}`).join(" · ") : "No hay seguidoras vinculadas.");
    } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); }
    finally { setBusy(false); }
  }
  async function saveSchedule(e: FormEvent) {
    e.preventDefault();
    try { await client!.setSchedule(sched); setRisk(await client!.risk()); setMsg("Horario guardado."); }
    catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); }
  }
  async function reopen() {
    if (!confirm("¿Levantar el bloqueo del cierre programado de hoy? Las copias se reanudan ahora.")) return;
    setRisk(await client!.reopenSession());
  }
  const body = (l: RiskLimit, trading_halted: boolean) => ({ account_id: l.account_id, max_daily_loss: l.max_daily_loss, max_daily_profit: l.max_daily_profit, max_position_size: l.max_position_size,
    max_trailing_drawdown: l.max_trailing_drawdown, drawdown_mode: l.drawdown_mode, drawdown_floor_cap: l.drawdown_floor_cap, drawdown_buffer: l.drawdown_buffer, trading_halted });
  function reset() { setAccount(""); setMaxLoss("0"); setMaxProfit("0"); setMaxSize("0"); setMaxDd("0"); setDdMode("intraday"); setDdCap("0"); setDdBuffer("0"); setHalted(false); }
  async function save(e: FormEvent) {
    e.preventDefault();
    try {
      await client!.upsertLimit({ account_id: account, max_daily_loss: Number(maxLoss) || 0, max_daily_profit: Number(maxProfit) || 0, max_position_size: Number(maxSize) || 0,
        max_trailing_drawdown: Number(maxDd) || 0, drawdown_mode: ddMode, drawdown_floor_cap: Number(ddCap) || 0, drawdown_buffer: Number(ddBuffer) || 0, trading_halted: halted });
      setRisk(await client!.risk()); reset();
    } catch (ex) { alert(ex instanceof Error ? ex.message : String(ex)); }
  }
  function edit(l: RiskLimit) {
    setAccount(l.account_id); setMaxLoss(String(l.max_daily_loss)); setMaxProfit(String(l.max_daily_profit)); setMaxSize(String(l.max_position_size)); setHalted(l.trading_halted);
    setMaxDd(String(l.max_trailing_drawdown)); setDdMode(l.drawdown_mode); setDdCap(String(l.drawdown_floor_cap)); setDdBuffer(String(l.drawdown_buffer));
    document.getElementById("limit-form")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  async function setPeak(id: string, current: number) {
    const raw = prompt(`Máximo (marca de agua) de ${id} para el drawdown.\n\nEscribe el valor que tiene el prop firm como máximo de la cuenta, o deja vacío para reiniciarlo al valor actual (evaluación nueva).`, String(current));
    if (raw === null) return;
    const v = raw.trim() === "" ? null : Number(raw.replace(",", "."));
    if (v !== null && (!isFinite(v) || v < 0)) { alert("Valor no válido."); return; }
    try { await client!.setPeak(id, v); } catch (ex) { alert(ex instanceof Error ? ex.message : String(ex)); }
  }
  async function remove(id: string) {
    if (!confirm(`¿Quitar los límites de ${id}? Volverá a copiar sin tope de pérdida, objetivo ni tamaño (y sin pausa).`)) return;
    try { await client!.deleteLimit(id); setRisk(await client!.risk()); if (account === id) setAccount(""); }
    catch (ex) { alert(ex instanceof Error ? ex.message : String(ex)); }
  }

  return (
    <div className="grid">
      <Card className={risk?.kill_switch ? "alert-card" : ""}>
        <div className="kill">
          <div>
            <h2>Kill switch global</h2>
            <p className="muted">{risk?.kill_switch ? `Activo desde ${time(risk.kill_switch_at)}${risk.kill_switch_reason ? ` · ${risk.kill_switch_reason}` : ""}` : "Inactivo. Las reglas replican con normalidad."}</p>
          </div>
          <div className="kill-actions">
            {!risk?.kill_switch && <>
              <label className="inline small"><input type="checkbox" checked={flattenOnKill} onChange={(e) => setFlattenOnKill(e.target.checked)} /> y cerrar posiciones</label>
              <label className="inline small"><input type="checkbox" checked={flattenMaster} disabled={!flattenOnKill} onChange={(e) => setFlattenMaster(e.target.checked)} /> incluida la maestra</label>
            </>}
            <button className={risk?.kill_switch ? "primary" : "danger-solid"} disabled={busy} onClick={toggleKill}>
              {risk?.kill_switch ? "Reanudar replicación" : "DETENER TODO"}
            </button>
          </div>
        </div>
      </Card>

      {risk?.addon_silent && (
        <Card className="alert-card"><strong>Sin heartbeat del addon.</strong> NinjaTrader no está enviando nada desde hace más de 15 s: las operaciones de la maestra no se están copiando. Revisa que NinjaTrader esté abierto y el addon cargado.</Card>
      )}
      {risk?.session_closed && (
        <Card className="alert-card"><div className="kill">
          <div><strong>Sesión cerrada por horario</strong> ({risk.schedule.flatten_at}). No se copia nada hasta mañana.</div>
          <button className="ghost" onClick={() => void reopen()}>Reabrir hoy</button></div></Card>
      )}

      <Card title="Horario de copia y cierre programado">
        <p className="muted small">Hora local del PC donde corre el engine. Fuera de la ventana no se copia nada. A la hora de cierre se cancelan órdenes, se cierran posiciones y se bloquea hasta el día siguiente. Útil para la regla de las prop firms de estar plano al cierre.</p>
        <form className="rule-form" onSubmit={saveSchedule}>
          <label className="inline"><input type="checkbox" checked={sched.enabled} onChange={(e) => setSched({ ...sched, enabled: e.target.checked })} /> Horario activo</label>
          <label>Copiar desde (HH:MM)<input type="time" value={sched.window_start} onChange={(e) => setSched({ ...sched, window_start: e.target.value })} /></label>
          <label>Cerrar todo a las (HH:MM)<input type="time" value={sched.flatten_at} onChange={(e) => setSched({ ...sched, flatten_at: e.target.value })} /></label>
          <label className="inline"><input type="checkbox" checked={sched.include_master} onChange={(e) => setSched({ ...sched, include_master: e.target.checked })} /> incluir la maestra</label>
          <button className="primary">Guardar horario</button>
        </form>
        {risk?.schedule.enabled && <p className="muted small">Activo: {risk.schedule.window_start ? `desde ${risk.schedule.window_start}` : "sin hora de inicio"}{risk.schedule.flatten_at ? ` · cierre a las ${risk.schedule.flatten_at}` : " · sin cierre programado"}{risk.schedule.last_flatten_day ? ` · último cierre ${risk.schedule.last_flatten_day}` : ""}</p>}
      </Card>

      <Card title="Cierre de emergencia">
        <p className="muted">Cancela todas las órdenes y cierra las posiciones a mercado en NinjaTrader. Requiere addon v1.5 o superior.</p>
        <div className="kill-actions">
          <button className="danger-solid" disabled={busy} onClick={() => void flattenAll(false)}>Cerrar todas las seguidoras</button>
          <button className="danger-solid" disabled={busy} onClick={() => void flattenAll(true)}>Cerrar TODO (incluida la maestra)</button>
        </div>
        {msg && <p className="muted small">{msg}</p>}
      </Card>

      <Card title="Drawdown dinámico · todas las cuentas" right={<span className="muted small">máximo = lo más que llegó a valer la cuenta (balance + flotante) desde que el engine la ve · se guarda entre reinicios</span>}>
        <p className="muted small">Así lo mide el prop firm: el <b>suelo</b> es el máximo menos el drawdown permitido y sube con cada máximo nuevo, nunca baja. Si la cuenta toca el suelo, el prop firm la cierra.
          Configura el <i>Drawdown máx.</i> en los límites de abajo para que el engine avise y cierre antes. <i>Ajustar máximo</i> sirve cuando el prop firm tiene otro máximo (la cuenta operó sin el engine) o al empezar una evaluación nueva.</p>
        {accounts.filter((a) => a.enabled || a.reported).length === 0 ? <Empty>Esperando cuentas del bróker…</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Cuenta</th><th className="num">Vale ahora</th><th className="num">Máximo</th><th className="num">Drawdown</th><th className="num">Límite</th><th className="num">Suelo</th><th className="num">Margen</th><th className="meter-cell">Consumido</th><th></th></tr></thead>
            <tbody>{[...accounts].filter((a) => a.enabled || a.reported).sort((x, y) => (y.drawdown.pct ?? -1) - (x.drawdown.pct ?? -1) || x.account_id.localeCompare(y.account_id)).map((a) => {
              const d = a.drawdown; const tone = ddTone(d);
              return (
                <tr key={a.account_id} data-dd={a.account_id}><td><b>{a.alias || a.account_id}</b>{a.alias && <span className="muted small"> {a.account_id}</span>}{!a.reported && <span className="muted small"> · no reportada</span>}</td>
                  <td className="num">{money(d.equity)}{a.unrealized_pnl ? <span className="muted small"> ({signedMoney(a.unrealized_pnl)} flot.)</span> : null}</td>
                  <td className="num">{money(d.peak)}<span className="muted small"> {d.peak_at ? time(d.peak_at) : ""}</span></td>
                  <td className={`num ${d.drawdown > 0 ? "bad" : "muted"}`}>{d.drawdown > 0 ? `−${money(d.drawdown)}` : "0"}</td>
                  <td className="num">{d.limit ? money(d.limit) : <span className="muted">— <span className="small">(solo se observa)</span></span>}</td>
                  <td className="num">{d.floor !== null ? <>{money(d.floor)}{d.locked && <span className="chip" title="El suelo llegó al tope configurado y ya no sube"> bloqueado</span>}</> : "—"}</td>
                  <td className={`num ${tone}`}>{d.room !== null ? money(d.room) : "—"}</td>
                  <td className="meter-cell">{d.pct !== null ? <DrawdownMeter d={d} /> : <span className="muted small">sin límite</span>}</td>
                  <td className="row-actions"><button className="ghost small-btn" onClick={() => void setPeak(a.account_id, d.peak)} title="Fijar el máximo a mano o reiniciarlo al valor actual">Ajustar máximo</button></td></tr>
              );
            })}</tbody>
          </table></div>
        )}
      </Card>

      <Card title="Límites por cuenta">
        <form className="rule-form" id="limit-form" onSubmit={save}>
          <label>Cuenta<input list="accts2" value={account} onChange={(e) => setAccount(e.target.value)} required /></label>
          <label>Pérdida diaria máx. ($)<input type="number" min="0" value={maxLoss} onChange={(e) => setMaxLoss(e.target.value)} /></label>
          <label>Ganancia diaria máx. ($)<input type="number" min="0" value={maxProfit} onChange={(e) => setMaxProfit(e.target.value)} /></label>
          <label>Posición máx. (contratos)<input type="number" min="0" value={maxSize} onChange={(e) => setMaxSize(e.target.value)} /></label>
          <label>Drawdown máx. del prop firm ($)<input type="number" min="0" value={maxDd} onChange={(e) => setMaxDd(e.target.value)} title="Cuánto puede caer la cuenta desde el máximo que llegó a valer antes de que el prop firm la cierre (APEX 50K: 2500)" /></label>
          <label>El máximo se mide<select value={ddMode} onChange={(e) => setDdMode(e.target.value as "intraday" | "closed")}>
            <option value="intraday">con el flotante (intradía: APEX, MFF…)</option><option value="closed">solo con lo cerrado (balance)</option></select></label>
          <label>El suelo se bloquea en ($)<input type="number" min="0" value={ddCap} onChange={(e) => setDdCap(e.target.value)} title="0 = el suelo sube siempre con el máximo. APEX: balance inicial + 100 (50K → 50100)" /></label>
          <label>Cerrar cuando falten ($)<input type="number" min="0" value={ddBuffer} onChange={(e) => setDdBuffer(e.target.value)} title="Colchón: el engine pausa y cierra la cuenta cuando le quedan estos dólares (o menos) hasta el suelo" /></label>
          <label className="inline"><input type="checkbox" checked={halted} onChange={(e) => setHalted(e.target.checked)} /> Pausar cuenta</label>
          <datalist id="accts2">{accounts.map((a) => <option key={a.account_id} value={a.account_id} />)}</datalist>
          <button className="primary">{risk?.limits.some((l) => l.account_id === account) ? "Guardar cambios" : "Guardar"}</button>
        </form>
        <p className="muted small">0 = sin límite. Al llegar a la pérdida máxima o a la ganancia máxima del día la cuenta se pausa y se cierra sola (la ganancia queda asegurada).
          El <b>drawdown máx.</b> es el trailing del prop firm: el suelo sube con el máximo de la cuenta y nunca baja; el engine avisa al 80 % y pausa y cierra la cuenta cuando faltan los dólares del colchón (ponle margen: el prop firm mide tick a tick y aquí se mira cada 2 s).</p>
        {!risk || risk.limits.length === 0 ? <Empty>Sin límites configurados (0 = sin límite).</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Cuenta</th><th className="num">Pérdida máx.</th><th className="num">Ganancia máx.</th><th className="num">P&L hoy</th><th className="num">Posición máx.</th><th className="num">Drawdown máx.</th><th>Estado</th><th></th></tr></thead>
            <tbody>{risk.limits.map((l) => {
              const acc = accounts.find((a) => a.account_id === l.account_id);
              const pnl = acc?.daily_pnl ?? 0;
              const ref = pnl < 0 ? l.max_daily_loss : l.max_daily_profit;
              const pct = ref ? Math.min(100, Math.max(0, (Math.abs(pnl) / ref) * 100)) : 0;
              const cls = !ref ? (pnl < 0 ? "warn" : "ok") : pnl < 0 ? (pct >= 80 ? "bad" : "warn") : (pct >= 80 ? "warn" : "ok");
              const badge = l.halted_reason === "daily_loss" ? "pausada · pérdida diaria" : l.halted_reason === "daily_profit" ? "pausada · objetivo de ganancia" : l.halted_reason === "drawdown" ? "pausada · drawdown" : "pausada";
              return (
                <tr key={l.account_id}><td><b>{l.account_id}</b></td><td className="num">{l.max_daily_loss || "—"}</td><td className="num">{l.max_daily_profit || "—"}</td>
                  <td className={`num ${cls}`}>{acc ? money(pnl) : "—"}{ref ? <span className="muted small"> ({pct.toFixed(0)} % {pnl < 0 ? "de la pérdida" : "del objetivo"})</span> : null}</td>
                  <td className="num">{l.max_position_size || "—"}</td>
                  <td className="num">{l.max_trailing_drawdown ? <>{l.max_trailing_drawdown}<span className="muted small"> {l.drawdown_mode === "closed" ? "cerrado" : "c/ flotante"}{l.drawdown_floor_cap ? ` · tope ${l.drawdown_floor_cap}` : ""}{l.drawdown_buffer ? ` · colchón ${l.drawdown_buffer}` : ""}</span></> : "—"}</td>
                  <td>{l.trading_halted ? <span className={`badge ${l.halted_reason === "daily_profit" ? "ok" : "bad"}`}>{badge}</span> : <span className="badge ok">activa</span>}</td>
                  <td className="row-actions">{l.trading_halted && <button className="ghost small-btn" onClick={() => void client!.upsertLimit(body(l, false)).then(() => client!.risk()).then(setRisk).catch((ex) => alert(ex instanceof Error ? ex.message : String(ex)))}>Reanudar</button>}
                    <button className="ghost small-btn" onClick={() => edit(l)} title="Cargar en el formulario para cambiar los límites">Editar</button>
                    <button className="ghost small-btn danger" onClick={() => void remove(l.account_id)} title="Quitar los límites de esta cuenta">Quitar</button></td></tr>
              );
            })}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
