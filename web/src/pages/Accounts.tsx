import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import { ago, money, time } from "../lib/format";
import { Card, Empty } from "../components/ui";
import type { Rule } from "../lib/api";

/** Panel de vinculación: cada cuenta conectada en NinjaTrader, con un interruptor
 *  para que copie al maestro y su multiplicador. Un clic = una regla. */
export function Accounts() {
  const { client, accounts, rules, setRules, risk, health, audit } = useStore();
  const detected = health?.bridge.master_account ?? null;
  const [master, setMaster] = useState<string>(detected ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (detected && !master) setMaster(detected); }, [detected, master]);
  if (!client) return null;

  const limits = new Map((risk?.limits ?? []).map((l) => [l.account_id, l]));
  const linkOf = (acc: string): Rule | undefined =>
    rules.find((r) => r.master_account.toLowerCase() === master.toLowerCase() && r.follower_account.toLowerCase() === acc.toLowerCase() && !r.symbol_filter);
  const lastFill = (acc: string) => audit.find((a) => a.event_type === "FOLLOWER_FILL" && a.target_account === acc);
  const lastReject = (acc: string) => audit.find((a) => a.event_type === "FOLLOWER_REJECTED" && a.target_account === acc);

  async function apply(acc: string, enabled: boolean, multiplier: number) {
    setBusy(acc); setErr(null);
    try {
      const r = await client!.link(acc, master, multiplier, enabled);
      setRules([...rules.filter((x) => x.id !== r.id), r]);
    } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); }
    finally { setBusy(null); }
  }
  async function remove(acc: string) {
    setBusy(acc);
    try { await client!.unlink(acc, master); setRules(rules.filter((r) => !(linkOf(acc)?.id === r.id))); }
    finally { setBusy(null); }
  }

  const followers = accounts.filter((a) => a.account_id.toLowerCase() !== master.toLowerCase());
  const masterAcc = accounts.find((a) => a.account_id.toLowerCase() === master.toLowerCase());
  const linked = followers.filter((a) => linkOf(a.account_id)?.enabled).length;

  return (
    <div className="grid">
      <Card title="Cuenta maestra" right={<span className="muted small">{detected ? "detectada del addon" : "sin heartbeat del addon"}</span>}>
        <div className="master-row">
          <select value={master} onChange={(e) => setMaster(e.target.value)}>
            {!master && <option value="">Selecciona…</option>}
            {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.account_id}{a.account_id === detected ? " · maestro del addon" : ""}</option>)}
          </select>
          {masterAcc && <span className="muted">{money(masterAcc.balance)} · {masterAcc.open_positions.length ? masterAcc.open_positions.map((p) => `${p.quantity > 0 ? "L" : "S"}${Math.abs(p.quantity)} ${p.symbol}`).join(", ") : "plana"}</span>}
        </div>
        {detected && master && master !== detected && (
          <p className="error">El addon de NinjaTrader publica las operaciones de <b>{detected}</b>. Para usar {master} como maestra cambia <code>MasterAccount</code> en el config.json del addon.</p>
        )}
        <p className="muted small">Se listan la maestra y las cuentas conectadas en tu sesión de NinjaTrader. Para incluir otras, usa <code>AccountFilter</code> en el config.json del addon.</p>
      </Card>

      <Card title={`Seguidoras · ${linked} de ${followers.length} copiando`}>
        {followers.length === 0 ? <Empty>Esperando cuentas del bróker…</Empty> : (
          <ul className="acct-list">
            {followers.map((a) => {
              const link = linkOf(a.account_id);
              const l = limits.get(a.account_id);
              const fill = lastFill(a.account_id); const rej = lastReject(a.account_id);
              const on = !!link?.enabled;
              return (
                <li key={a.account_id} className={`acct ${on ? "on" : ""}`}>
                  <div className="acct-main">
                    <div className="acct-name"><b>{a.account_id}</b>
                      {l?.trading_halted && <span className="badge bad">pausada</span>}
                      {on && <span className="badge ok">copiando</span>}
                    </div>
                    <div className="acct-meta">
                      <span>{money(a.balance)}</span>
                      <span className="muted">{a.open_positions.length ? a.open_positions.map((p) => `${p.quantity > 0 ? "L" : "S"}${Math.abs(p.quantity)} ${p.symbol}`).join(", ") : "plana"}</span>
                      <span className="muted">{ago(a.updated_at)}</span>
                    </div>
                    {(fill || rej) && (
                      <div className="acct-last">
                        {rej && (!fill || rej.timestamp > fill.timestamp)
                          ? <span className="bad">Rechazo {time(rej.timestamp)}: {rej.message.replace(`${a.account_id}: `, "")}</span>
                          : fill && <span className="muted">Último fill {time(fill.timestamp)}: {fill.message.replace(`${a.account_id}: `, "")}</span>}
                      </div>
                    )}
                  </div>
                  <div className="acct-actions">
                    <label className="mult">x<input type="number" step="0.1" min="0.1" value={link?.multiplier ?? 1} disabled={busy === a.account_id || !master}
                      onChange={(e) => { const m = Number(e.target.value); if (m > 0 && link) void apply(a.account_id, link.enabled, m); }}
                      onBlur={(e) => { const m = Number(e.target.value); if (m > 0 && !link) void apply(a.account_id, false, m); }} /></label>
                    <label className="switch" title={on ? "Dejar de copiar" : "Copiar al maestro"}>
                      <input type="checkbox" checked={on} disabled={busy === a.account_id || !master}
                        onChange={(e) => void apply(a.account_id, e.target.checked, link?.multiplier ?? 1)} /><span />
                    </label>
                    {link && <button className="ghost danger" disabled={busy === a.account_id} onClick={() => void remove(a.account_id)} title="Quitar vínculo">✕</button>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {err && <p className="error">{err}</p>}
        <p className="muted small">Las reglas con filtro de símbolo se gestionan en <i>Copiar</i>; aquí solo los vínculos completos.</p>
      </Card>
    </div>
  );
}
