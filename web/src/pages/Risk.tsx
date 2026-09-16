import { useState, type FormEvent } from "react";
import { useStore } from "../lib/store";
import { time } from "../lib/format";
import { Card, Empty } from "../components/ui";

export function Risk() {
  const { client, risk, setRisk, accounts } = useStore();
  const [account, setAccount] = useState("");
  const [maxLoss, setMaxLoss] = useState("0");
  const [maxSize, setMaxSize] = useState("0");
  const [halted, setHalted] = useState(false);
  if (!client) return null;

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
  async function save(e: FormEvent) {
    e.preventDefault();
    await client!.upsertLimit({ account_id: account, max_daily_loss: Number(maxLoss) || 0, max_position_size: Number(maxSize) || 0, trading_halted: halted });
    setRisk(await client!.risk()); setAccount("");
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

      <Card title="Cierre de emergencia">
        <p className="muted">Cancela todas las órdenes y cierra las posiciones a mercado en NinjaTrader. Requiere addon v1.5 o superior.</p>
        <div className="kill-actions">
          <button className="danger-solid" disabled={busy} onClick={() => void flattenAll(false)}>Cerrar todas las seguidoras</button>
          <button className="danger-solid" disabled={busy} onClick={() => void flattenAll(true)}>Cerrar TODO (incluida la maestra)</button>
        </div>
        {msg && <p className="muted small">{msg}</p>}
      </Card>

      <Card title="Límites por cuenta">
        <form className="rule-form" onSubmit={save}>
          <label>Cuenta<input list="accts2" value={account} onChange={(e) => setAccount(e.target.value)} required /></label>
          <label>Pérdida diaria máx. ($)<input type="number" min="0" value={maxLoss} onChange={(e) => setMaxLoss(e.target.value)} /></label>
          <label>Tamaño máx. por orden<input type="number" min="0" value={maxSize} onChange={(e) => setMaxSize(e.target.value)} /></label>
          <label className="inline"><input type="checkbox" checked={halted} onChange={(e) => setHalted(e.target.checked)} /> Pausar cuenta</label>
          <datalist id="accts2">{accounts.map((a) => <option key={a.account_id} value={a.account_id} />)}</datalist>
          <button className="primary">Guardar</button>
        </form>
        {!risk || risk.limits.length === 0 ? <Empty>Sin límites configurados (0 = sin límite).</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Cuenta</th><th className="num">Pérdida máx.</th><th className="num">Tamaño máx.</th><th>Estado</th></tr></thead>
            <tbody>{risk.limits.map((l) => (
              <tr key={l.account_id}><td><b>{l.account_id}</b></td><td className="num">{l.max_daily_loss || "—"}</td><td className="num">{l.max_position_size || "—"}</td>
                <td>{l.trading_halted ? <span className="badge bad">pausada</span> : <span className="badge ok">activa</span>}</td></tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
    </div>
  );
}
