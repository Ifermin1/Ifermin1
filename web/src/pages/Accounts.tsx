import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import { ago, money, time } from "../lib/format";
import { Card, Empty } from "../components/ui";
import type { Account, ExecOptions, Rule } from "../lib/api";

const posText = (a: Account) => a.open_positions.length ? a.open_positions.map((p) => `${p.quantity > 0 ? "L" : "S"}${Math.abs(p.quantity)} ${p.symbol}`).join(", ") : "plana";
const label = (a: Account) => a.alias ? `${a.alias} · ${a.account_id}` : a.account_id;

function ConnBadge({ a }: { a: Account }) {
  if (!a.reported) return <span className="badge muted">no reportada</span>;
  if (a.connected === null) return <span className="badge muted">{a.connection || "sin estado"}</span>;
  return <span className={`badge ${a.connected ? "ok" : "bad"}`}>{a.connected ? "conectada" : "desconectada"}{a.connection ? ` · ${a.connection}` : ""}</span>;
}

/** Cuentas: la maestra, las seguidoras activas con su interruptor de copia, y un
 *  panel de gestión con todas las cuentas que NinjaTrader conoce. */
export function Accounts() {
  const { client, accounts, rules, setRules, risk, health, audit } = useStore();
  const detected = health?.bridge.master_account ?? null;
  const [master, setMaster] = useState<string>(detected ?? "");
  const [manage, setManage] = useState(false);
  const [openOpts, setOpenOpts] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<"connected" | "enabled" | "all">("connected");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (detected && !master) setMaster(detected); }, [detected, master]);
  if (!client) return null;

  const limits = new Map((risk?.limits ?? []).map((l) => [l.account_id, l]));
  const linkOf = (acc: string): Rule | undefined =>
    rules.find((r) => r.master_account.toLowerCase() === master.toLowerCase() && r.follower_account.toLowerCase() === acc.toLowerCase() && !r.symbol_filter);
  const lastFill = (acc: string) => audit.find((a) => a.event_type === "FOLLOWER_FILL" && a.target_account === acc);
  const lastReject = (acc: string) => audit.find((a) => a.event_type === "FOLLOWER_REJECTED" && a.target_account === acc);

  async function guard(acc: string, fn: () => Promise<void>) {
    setBusy(acc); setErr(null);
    try { await fn(); } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  }
  const apply = (acc: string, enabled: boolean, multiplier: number, opts: ExecOptions = {}) => guard(acc, async () => {
    const r = await client!.link(acc, master, multiplier, enabled, opts); setRules([...rules.filter((x) => x.id !== r.id), r]);
  });
  const remove = (acc: string) => guard(acc, async () => { const id = linkOf(acc)?.id; await client!.unlink(acc, master); setRules(rules.filter((r) => r.id !== id)); });
  const setEnabled = (acc: string, enabled: boolean) => guard(acc, async () => { await client!.setAccount(acc, { enabled }); });
  const setAuto = (acc: string) => guard(acc, async () => { await client!.setAccount(acc, { auto: true }); });
  const setAlias = (acc: string, alias: string) => guard(acc, async () => { await client!.setAccount(acc, { alias }); });
  const changeMaster = (acc: string) => guard("__master", async () => {
    if (!acc || acc === detected) { setMaster(acc); return; }
    if (!confirm(`¿Cambiar la cuenta maestra a ${acc}?\n\nA partir de ahora se replicarán las operaciones de ${acc}. Las cuentas que copiaban a ${detected ?? "la anterior"} dejan de copiar hasta que las vincules a la nueva.`)) return;
    const r = await client!.setMaster(acc); setMaster(r.master_account);
  });
  const flatten = (acc: string) => guard(acc, async () => {
    if (!confirm(`¿CERRAR ${acc}?\n\nSe cancelan todas sus órdenes y se cierra la posición a mercado.`)) return;
    await client!.flatten(acc, "manual desde consola");
  });
  const resync = (acc: string) => guard(acc, async () => {
    if (!confirm(`¿Igualar ${acc} a la maestra?\n\nSe manda a mercado la diferencia de contratos.`)) return;
    const r = await client!.resync(acc);
    if (r.sent.length === 0) alert("Ya coincide con la maestra.");
  });
  const forget = (acc: string) => guard(acc, async () => { if (confirm(`¿Olvidar ${acc}? Volverá a aparecer si NinjaTrader la reporta.`)) await client!.forgetAccount(acc); });

  const masterAcc = accounts.find((a) => a.account_id.toLowerCase() === master.toLowerCase());
  const followers = accounts.filter((a) => a.account_id.toLowerCase() !== master.toLowerCase() && a.enabled);
  const hidden = accounts.filter((a) => !a.enabled).length;
  const linked = followers.filter((a) => linkOf(a.account_id)?.enabled).length;
  const connectedCount = accounts.filter((a) => a.connected).length;
  const enabledCount = accounts.filter((a) => a.enabled).length;
  const rank = (a: Account) => (a.connected ? 0 : a.enabled ? 1 : a.reported ? 2 : 3);
  const needle = q.trim().toLowerCase();
  const managed = accounts
    .filter((a) => filter === "all" || (filter === "connected" ? !!a.connected : a.enabled))
    .filter((a) => !needle || a.account_id.toLowerCase().includes(needle) || a.alias.toLowerCase().includes(needle) || a.connection.toLowerCase().includes(needle))
    .sort((x, y) => rank(x) - rank(y) || x.account_id.localeCompare(y.account_id));

  return (
    <div className="grid">
      <Card title="Cuenta maestra" right={<span className="muted small">{detected ? "detectada del addon" : "sin heartbeat del addon"}</span>}>
        <div className="master-row">
          <select value={master} disabled={busy === "__master"} onChange={(e) => void changeMaster(e.target.value)}>
            {!master && <option value="">Selecciona…</option>}
            {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{label(a)}{a.account_id === detected ? " · maestro del addon" : ""}</option>)}
          </select>
          {masterAcc && <><ConnBadge a={masterAcc} /><span className="muted">{money(masterAcc.balance)} · {posText(masterAcc)}</span>
            {masterAcc.open_positions.length > 0 && <button className="danger-solid small-btn" disabled={busy === masterAcc.account_id} onClick={() => void flatten(masterAcc.account_id)}>Cerrar maestra</button>}</>}
        </div>
        {detected && master && master !== detected && (
          <p className="error">El addon sigue publicando <b>{detected}</b>; el cambio a {master} no se aplicó.</p>
        )}
        {err && busy === null && <p className="error">{err}</p>}
        <p className="muted small">Al elegir otra cuenta, el engine se lo pide a NinjaTrader y el addon la guarda: sobrevive a reinicios. Requiere addon v1.2 o superior.</p>
      </Card>

      <Card title={`Seguidoras · ${linked} de ${followers.length} copiando`}
            right={<button className="ghost" onClick={() => setManage(!manage)}>{manage ? "Cerrar gestión" : `Gestionar cuentas${hidden ? ` (${hidden} ocultas)` : ""}`}</button>}>
        {followers.length === 0 ? <Empty>{accounts.length ? "Todas las cuentas están desactivadas. Actívalas en “Gestionar cuentas”." : "Esperando cuentas del bróker…"}</Empty> : (
          <ul className="acct-list">
            {followers.map((a) => {
              const link = linkOf(a.account_id); const l = limits.get(a.account_id);
              const fill = lastFill(a.account_id); const rej = lastReject(a.account_id); const on = !!link?.enabled;
              return (
                <li key={a.account_id} className={`acct ${on ? "on" : ""} ${a.connected === false ? "offline" : ""}`}>
                  <div className="acct-main">
                    <div className="acct-name"><b>{a.alias || a.account_id}</b>{a.alias && <span className="muted small">{a.account_id}</span>}
                      <ConnBadge a={a} />{l?.trading_halted && <span className="badge bad">pausada</span>}{on && <span className="badge ok">copiando</span>}
                      {link?.target_root && <span className="chip">→ {link.target_root}</span>}{link?.entry_mode === "limit" && <span className="chip">límite ±{link.tolerance_ticks}</span>}
                    </div>
                    <div className="acct-meta"><span>{money(a.balance)}</span>
                      <span className={a.daily_pnl < 0 ? "bad" : a.daily_pnl > 0 ? "ok" : "muted"}>P&L {money(a.daily_pnl)}</span>
                      <span className={a.open_positions.length ? "" : "muted"}>{posText(a)}</span><span className="muted">{ago(a.updated_at)}</span></div>
                    {(fill || rej) && (
                      <div className="acct-last">
                        {rej && (!fill || rej.timestamp > fill.timestamp)
                          ? <span className="bad">Rechazo {time(rej.timestamp)}: {rej.message.replace(`${a.account_id}: `, "")}</span>
                          : fill && <span className="muted">Último fill {time(fill.timestamp)}: {fill.message.replace(`${a.account_id}: `, "")}</span>}
                      </div>
                    )}
                    {on && a.connected === false && <div className="acct-last warn">Copiando pero desconectada: NinjaTrader rechazará las órdenes hasta que conecte.</div>}
                    {a.desync && <div className="acct-last bad">DESINCRONIZADA: {a.desync_detail}. Solo se copian salidas hasta igualarla.
                      <button className="ghost small-btn" disabled={busy === a.account_id} onClick={() => void resync(a.account_id)}>Igualar a la maestra</button></div>}
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
                    {a.open_positions.length > 0 && <button className="danger-solid small-btn" disabled={busy === a.account_id} onClick={() => void flatten(a.account_id)} title="Cancelar órdenes y cerrar posición">Cerrar</button>}
                    <button className="ghost small-btn" title="Opciones de ejecución" onClick={() => setOpenOpts(openOpts === a.account_id ? null : a.account_id)}>⚙</button>
                  </div>
                  {openOpts === a.account_id && (
                    <div className="exec-opts">
                      <label>Símbolo destino<input placeholder="igual que la maestra · ej. MNQ" defaultValue={link?.target_root ?? ""}
                        onBlur={(e) => void apply(a.account_id, link?.enabled ?? false, link?.multiplier ?? 1, { target_root: e.target.value.trim() })} /></label>
                      <label>Entrada<select value={link?.entry_mode ?? "market"} onChange={(e) => void apply(a.account_id, link?.enabled ?? false, link?.multiplier ?? 1, { entry_mode: e.target.value as "market" | "limit" })}>
                        <option value="market">A mercado (copia inmediata)</option><option value="limit">Límite al precio del maestro ± ticks</option></select></label>
                      {(link?.entry_mode ?? "market") === "limit" && <>
                        <label>Tolerancia (ticks)<input type="number" min="0" max="50" defaultValue={link?.tolerance_ticks ?? 2}
                          onBlur={(e) => void apply(a.account_id, link?.enabled ?? false, link?.multiplier ?? 1, { tolerance_ticks: Number(e.target.value) })} /></label>
                        <label>Espera (s)<input type="number" min="1" max="120" defaultValue={link?.entry_timeout_s ?? 5}
                          onBlur={(e) => void apply(a.account_id, link?.enabled ?? false, link?.multiplier ?? 1, { entry_timeout_s: Number(e.target.value) })} /></label>
                        <label>Si no se llena<select value={link?.entry_fallback ?? "market"} onChange={(e) => void apply(a.account_id, link?.enabled ?? false, link?.multiplier ?? 1, { entry_fallback: e.target.value as "market" | "cancel" })}>
                          <option value="market">A mercado lo que falte</option><option value="cancel">Cancelar (no entrar)</option></select></label>
                      </>}
                      <p className="muted small">Las salidas (stops, take profits y cierres) van siempre a mercado o con su propia orden. Símbolo destino: la seguidora opera ese contrato (p. ej. maestra NQ, seguidora MNQ con multiplicador ×10). Requiere addon v1.7 para entradas límite.</p>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {err && <p className="error">{err}</p>}
      </Card>

      {manage && (
        <Card title={`Todas las cuentas · ${accounts.length} conocidas, ${connectedCount} conectadas, ${enabledCount} activas`}>
          <p className="muted small">Todo lo que NinjaTrader conoce, conectado o no. Por defecto una cuenta está <b>activa mientras está conectada</b> y se oculta al desconectarse (modo <i>auto</i>). Si tocas su interruptor queda fijada a mano; con <i>auto</i> vuelve a la política automática. Una cuenta oculta nunca recibe copias.</p>
          <div className="manage-bar">
            <input placeholder="Buscar cuenta, alias o conexión…" value={q} onChange={(e) => setQ(e.target.value)} />
            <div className="chips">
              {([["connected", `Conectadas (${connectedCount})`], ["enabled", `Activas (${enabledCount})`], ["all", `Todas (${accounts.length})`]] as const).map(([k, l]) => (
                <button key={k} className={`chip-btn ${filter === k ? "active" : ""}`} onClick={() => setFilter(k)}>{l}</button>))}
            </div>
          </div>
          {managed.length === 0 && <Empty>Nada que mostrar con este filtro.</Empty>}
          <div className="table-wrap"><table className="manage">
            <thead><tr><th>Activa</th><th>Cuenta</th><th>Alias</th><th>Conexión</th><th className="num">Saldo</th><th>Vista</th><th></th></tr></thead>
            <tbody>{managed.slice(0, 300).map((a) => (
              <tr key={a.account_id} className={a.enabled ? "" : "off"}>
                <td><div className="act-cell"><label className="switch small"><input type="checkbox" checked={a.enabled} disabled={busy === a.account_id || a.account_id === master}
                  onChange={(e) => void setEnabled(a.account_id, e.target.checked)} /><span /></label>
                  {a.enabled_source === "user" ? <button className="link tiny" title="Volver a automático" onClick={() => void setAuto(a.account_id)}>fijada · auto</button> : <span className="muted tiny">auto</span>}</div></td>
                <td><b>{a.account_id}</b>{a.account_id === master && <span className="badge ok">maestra</span>}</td>
                <td><input className="alias" defaultValue={a.alias} placeholder="p. ej. Eval MFF 50k" maxLength={40}
                  onBlur={(e) => { if (e.target.value !== a.alias) void setAlias(a.account_id, e.target.value); }} /></td>
                <td><ConnBadge a={a} /></td>
                <td className="num">{money(a.balance)}</td>
                <td className="muted nowrap">{ago(a.updated_at)}</td>
                <td>{!a.reported && <button className="ghost danger" onClick={() => void forget(a.account_id)}>Olvidar</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
          {managed.length > 300 && <p className="muted small">Mostrando 300 de {managed.length}. Afina la búsqueda.</p>}
        </Card>
      )}
    </div>
  );
}
