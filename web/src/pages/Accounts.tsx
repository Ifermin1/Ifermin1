import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import { ago, money } from "../lib/format";
import { Card, Empty } from "../components/ui";
import { AccountPanel } from "../components/AccountPanel";
import { ProfileEditor, type ProfileResult } from "../components/ProfileEditor";
import type { Account, ExecOptions, Rule } from "../lib/api";

const label = (a: Account) => a.alias ? `${a.alias} · ${a.account_id}` : a.account_id;

function ConnBadge({ a }: { a: Account }) {
  if (!a.reported) return <span className="badge muted">no reportada</span>;
  if (a.connected === null) return <span className="badge muted">{a.connection || "sin estado"}</span>;
  return <span className={`badge ${a.connected ? "ok" : "bad"}`}>{a.connected ? "conectada" : "desconectada"}{a.connection ? ` · ${a.connection}` : ""}</span>;
}

/** Cuentas: la maestra, las seguidoras activas con su interruptor de copia, y un
 *  panel de gestión con todas las cuentas que NinjaTrader conoce. */
export function Accounts() {
  const { client, accounts, rules, setRules, risk, setRisk, health, audit, prices } = useStore();
  const detected = health?.bridge.master_account ?? null;
  const [master, setMaster] = useState<string>(detected ?? "");
  const [manage, setManage] = useState(false);
  const [openOpts, setOpenOpts] = useState<string | null>(null);
  const [openProfile, setOpenProfile] = useState<string | null>(null);   // cuenta con el editor de prop firm abierto
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<"connected" | "enabled" | "hidden" | "all">("connected");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msel, setMsel] = useState<Set<string>>(new Set());   // cuentas marcadas en la gestión (acciones en lote)
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
  /** Objetivo de ganancia del día (mismo límite que en Riesgo): conserva el resto de límites de la cuenta. */
  const targetBody = (acc: string, target: number) => {
    const l = limits.get(acc);
    return { account_id: acc, max_daily_loss: l?.max_daily_loss ?? 0, max_daily_profit: target, profit_goal: l?.profit_goal ?? 0, start_balance: l?.start_balance ?? 0,
             max_position_size: l?.max_position_size ?? 0,
             max_trailing_drawdown: l?.max_trailing_drawdown ?? 0, drawdown_mode: l?.drawdown_mode ?? "intraday" as const, drawdown_floor_cap: l?.drawdown_floor_cap ?? 0,
             drawdown_buffer: l?.drawdown_buffer ?? 0, trading_halted: !!l?.trading_halted && l.halted_reason !== "daily_profit" };
  };
  /** Objetivo de la evaluación (acumulado sobre el saldo inicial); conserva el resto de límites. */
  const goalBody = (acc: string, goal: number, start: number) => {
    const l = limits.get(acc);
    return { ...targetBody(acc, l?.max_daily_profit ?? 0), profit_goal: goal, start_balance: goal > 0 ? start : (l?.start_balance ?? 0),
             trading_halted: !!l?.trading_halted && l.halted_reason !== "profit_goal" };
  };
  const setGoal = (acc: string, goal: number, start: number) => guard(acc, async () => {
    if (goal > 0 && !(start > 0)) { const v = prompt(`Saldo inicial de ${acc} (desde dónde se cuenta el objetivo):`, String(Math.round(accounts.find((a) => a.account_id === acc)?.balance ?? 0))); if (v === null) return; start = Number(v.replace(",", ".")) || 0; if (!(start > 0)) throw new Error("Saldo inicial no válido."); }
    await client!.upsertLimit(goalBody(acc, goal, start)); setRisk(await client!.risk());
  });
  const setGoalAll = () => guard("__goal", async () => {
    const ids = followers.map((a) => a.account_id);
    if (!ids.length) return;
    const v = prompt(`Objetivo de la evaluación para ${ids.length} seguidoras (ganancia neta acumulada sobre el saldo inicial; 0 = sin objetivo). Al alcanzarlo, el engine cierra y deja de copiar a esa cuenta:`, String(limits.get(ids[0])?.profit_goal || ""));
    if (v === null) return;
    const goal = Math.max(0, Number(v.replace(",", ".")) || 0);
    const errors: string[] = [];
    for (const id of ids) {
      const a = accounts.find((x) => x.account_id === id)!;
      const start = limits.get(id)?.start_balance || a.plan_size || Math.round(a.balance - a.net_pnl);
      try { await client!.upsertLimit(goalBody(id, goal, start)); } catch (ex) { errors.push(`${id}: ${ex instanceof Error ? ex.message : String(ex)}`); }
    }
    setRisk(await client!.risk());
    if (errors.length) throw new Error(errors.join("; "));
  });
  /** Perfil de prop firm: guarda firma/plan/tamaño en la cuenta y los límites en Riesgo (para varias cuentas a la vez). */
  const applyProfile = async (acc: string, r: ProfileResult, alsoTo: string[]) => {
    setBusy(acc); setErr(null);
    try {
      const errors: string[] = [];
      for (const id of [acc, ...alsoTo]) {
        try {
          await client!.setAccount(id, { firm: r.firm, plan: r.plan, plan_size: r.size });
          if (r.firm) {
            const l = limits.get(id);
            await client!.upsertLimit({ account_id: id, max_daily_loss: r.dailyLoss, max_daily_profit: r.dayCut, profit_goal: r.target, start_balance: r.target > 0 ? r.size : (l?.start_balance ?? 0), max_position_size: r.contracts,
                                        max_trailing_drawdown: r.drawdown, drawdown_mode: r.dd, drawdown_floor_cap: r.cap, drawdown_buffer: r.buffer,
                                        trading_halted: !!l?.trading_halted && !!l.halted_reason && l.halted_reason !== "daily_profit" });
          }
        } catch (ex) { errors.push(`${id}: ${ex instanceof Error ? ex.message : String(ex)}`); }
      }
      setRisk(await client!.risk());
      if (errors.length) throw new Error(errors.join("; "));
    } finally { setBusy(null); }
  };
  const setTarget = (acc: string, target: number) => guard(acc, async () => {
    await client!.upsertLimit(targetBody(acc, target)); setRisk(await client!.risk());
  });
  const setTargetAll = () => guard("__target", async () => {
    const ids = followers.map((a) => a.account_id);
    if (!ids.length) return;
    const v = prompt(`Corte de ganancias del día (NETO, descontadas comisiones) para ${ids.length} seguidoras (0 = sin corte). Al alcanzarlo, el engine cierra la posición de esa cuenta y deja de copiarle hasta mañana:`, String(limits.get(ids[0])?.max_daily_profit || ""));
    if (v === null) return;
    const target = Math.max(0, Number(v.replace(",", ".")) || 0);
    const errors: string[] = [];
    for (const id of ids) { try { await client!.upsertLimit(targetBody(id, target)); } catch (ex) { errors.push(`${id}: ${ex instanceof Error ? ex.message : String(ex)}`); } }
    setRisk(await client!.risk());
    if (errors.length) throw new Error(errors.join("; "));
  });
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
  const toggleMsel = (id: string) => setMsel((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  /** Vincula varias cuentas a la maestra de golpe: pregunta un multiplicador para las que no tienen regla (las que ya la
   *  tienen conservan el suyo y solo se activan). */
  const linkMany = (ids: string[], what: string) => guard("__all", async () => {
    if (!master) { alert("Elige primero la cuenta maestra."); return; }
    const targets = ids.filter((id) => id.toLowerCase() !== master.toLowerCase() && !linkOf(id)?.enabled);
    if (!targets.length) { alert(`${what}: ya copian todas a ${master}.`); return; }
    const v = prompt(`Vincular ${targets.length} cuentas a ${master}:\n${targets.join(", ")}\n\nMultiplicador para las que no tienen regla (las que ya la tienen conservan el suyo):`, "1");
    if (v === null) return;
    const m = Number(v.replace(",", "."));
    if (!(m > 0)) { alert("Multiplicador no válido."); return; }
    const errors: string[] = []; let next = rules;
    for (const id of targets) {
      const prev = linkOf(id);
      try { const r = await client!.link(id, master, prev?.multiplier ?? m, true); next = [...next.filter((x) => x.id !== r.id), r]; }
      catch (ex) { errors.push(`${id}: ${ex instanceof Error ? ex.message : String(ex)}`); }
    }
    setRules(next); setMsel(new Set());
    if (errors.length) throw new Error(errors.join("; "));
  });
  const bulkAccounts = (action: "enable" | "disable" | "auto" | "forget") => guard("__bulk", async () => {
    const ids = [...msel].filter((id) => id !== master && (action !== "forget" || !accounts.find((a) => a.account_id === id)?.reported));
    if (!ids.length) { alert(action === "forget" ? "Solo se pueden olvidar cuentas que NinjaTrader ya no reporta." : "Nada que hacer con las marcadas."); return; }
    const ask = { enable: `¿Mostrar ${ids.length} cuentas? Vuelven a Inicio, Cuentas y Copiar y pueden recibir copias.`, disable: `¿Ocultar ${ids.length} cuentas? Desaparecen de Inicio, Cuentas y Copiar y nunca reciben copias.`,
                  auto: `¿Volver a modo automático ${ids.length} cuentas? (activa = conectada)`, forget: `¿Olvidar ${ids.length} cuentas que NinjaTrader ya no reporta?` }[action];
    if (!confirm(`${ask}\n\n${ids.join(", ")}`)) return;
    const errors: string[] = [];
    for (const id of ids) {
      try {
        if (action === "forget") await client!.forgetAccount(id);
        else if (action === "auto") await client!.setAccount(id, { auto: true });
        else await client!.setAccount(id, { enabled: action === "enable" });
      } catch (ex) { errors.push(`${id}: ${ex instanceof Error ? ex.message : String(ex)}`); }
    }
    setMsel(new Set());
    if (errors.length) throw new Error(errors.join("; "));
  });

  const masterAcc = accounts.find((a) => a.account_id.toLowerCase() === master.toLowerCase());
  const followers = accounts.filter((a) => a.account_id.toLowerCase() !== master.toLowerCase() && a.enabled);
  const hidden = accounts.filter((a) => !a.enabled).length;
  const linked = followers.filter((a) => linkOf(a.account_id)?.enabled).length;
  const connectedCount = accounts.filter((a) => a.connected).length;
  const enabledCount = accounts.filter((a) => a.enabled).length;
  const rank = (a: Account) => (a.connected ? 0 : a.enabled ? 1 : a.reported ? 2 : 3);
  const needle = q.trim().toLowerCase();
  const managed = accounts
    .filter((a) => filter === "all" || (filter === "connected" ? !!a.connected : filter === "hidden" ? !a.enabled : a.enabled))
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
        </div>
        {masterAcc && <div className="panels one"><AccountPanel a={masterAcc} role="master" prices={prices} busy={busy === masterAcc.account_id} limit={limits.get(masterAcc.account_id)}
                                                       onFlatten={() => void flatten(masterAcc.account_id)} onTarget={(t) => void setTarget(masterAcc.account_id, t)} onGoal={(g, s) => void setGoal(masterAcc.account_id, g, s)}
                                                       controls={<button className={`ghost small-btn ${openProfile === masterAcc.account_id ? "active" : ""}`} onClick={() => setOpenProfile(openProfile === masterAcc.account_id ? null : masterAcc.account_id)} data-testid={`profile-${masterAcc.account_id}`}>Prop firm</button>}>
          {openProfile === masterAcc.account_id && <ProfileEditor a={masterAcc} limit={limits.get(masterAcc.account_id)} followers={followers} busy={busy === masterAcc.account_id}
                                                                  onApply={(r, also) => applyProfile(masterAcc.account_id, r, also)} onClose={() => setOpenProfile(null)} />}
        </AccountPanel></div>}
        {detected && master && master !== detected && (
          <p className="error">El addon sigue publicando <b>{detected}</b>; el cambio a {master} no se aplicó.</p>
        )}
        {err && busy === null && <p className="error">{err}</p>}
        <p className="muted small">Al elegir otra cuenta, el engine se lo pide a NinjaTrader y el addon la guarda: sobrevive a reinicios. Requiere addon v1.2 o superior.</p>
      </Card>

      <Card title={`Seguidoras · ${linked} de ${followers.length} copiando`}
            right={<div className="chips">
              {followers.length > 0 && <button className="primary small-btn" disabled={busy === "__all" || !master} onClick={() => void linkMany(followers.map((a) => a.account_id), "Seguidoras activas")} data-testid="link-all">Vincular todas</button>}
              {followers.length > 0 && <button className="small-btn" disabled={busy === "__target"} onClick={() => void setTargetAll()} title="Mismo corte de ganancias del día (neto) para todas las seguidoras" data-testid="target-all">Corte para todas</button>}
              {followers.length > 0 && <button className="small-btn" disabled={busy === "__goal"} onClick={() => void setGoalAll()} title="Mismo objetivo de evaluación (acumulado) para todas las seguidoras" data-testid="goal-all">Objetivo para todas</button>}
              <button className="ghost" onClick={() => setManage(!manage)}>{manage ? "Cerrar gestión" : `Gestionar cuentas${hidden ? ` (${hidden} ocultas)` : ""}`}</button>
            </div>}>
        {followers.length === 0 ? <Empty>{accounts.length ? "Todas las cuentas están desactivadas. Actívalas en “Gestionar cuentas”." : "Esperando cuentas del bróker…"}</Empty> : (
          <div className="panels">
            {followers.map((a) => {
              const link = linkOf(a.account_id);
              return (
                <AccountPanel key={a.account_id} a={a} role="follower" link={link} limit={limits.get(a.account_id)} prices={prices} busy={busy === a.account_id}
                              lastFill={lastFill(a.account_id)} lastReject={lastReject(a.account_id)}
                              onFlatten={() => void flatten(a.account_id)} onResync={() => void resync(a.account_id)} onTarget={(t) => void setTarget(a.account_id, t)} onGoal={(g, s) => void setGoal(a.account_id, g, s)}
                              controls={<>
                                <label className="mult">x<input type="number" step="0.1" min="0.1" value={link?.multiplier ?? 1} disabled={busy === a.account_id || !master}
                                  onChange={(e) => { const m = Number(e.target.value); if (m > 0 && link) void apply(a.account_id, link.enabled, m); }}
                                  onBlur={(e) => { const m = Number(e.target.value); if (m > 0 && !link) void apply(a.account_id, false, m); }} /></label>
                                <label className="switch" title={link?.enabled ? "Dejar de copiar" : "Copiar al maestro"}>
                                  <input type="checkbox" checked={!!link?.enabled} disabled={busy === a.account_id || !master}
                                    onChange={(e) => void apply(a.account_id, e.target.checked, link?.multiplier ?? 1)} /><span />
                                </label>
                                {link && <button className="ghost danger small-btn" disabled={busy === a.account_id} onClick={() => void remove(a.account_id)} title="Quitar vínculo">✕</button>}
                                <button className={`ghost small-btn ${openProfile === a.account_id ? "active" : ""}`} title="Prop firm y tipo de cuenta (drawdown, objetivo, contratos)" onClick={() => { setOpenProfile(openProfile === a.account_id ? null : a.account_id); setOpenOpts(null); }} data-testid={`profile-${a.account_id}`}>Prop firm</button>
                                <button className={`ghost small-btn ${openOpts === a.account_id ? "active" : ""}`} title="Opciones de ejecución" onClick={() => setOpenOpts(openOpts === a.account_id ? null : a.account_id)}>⚙</button>
                              </>}>
                  {openProfile === a.account_id && <ProfileEditor a={a} limit={limits.get(a.account_id)} followers={followers.filter((x) => x.account_id !== a.account_id)} busy={busy === a.account_id}
                                                                 onApply={(r, also) => applyProfile(a.account_id, r, also)} onClose={() => setOpenProfile(null)} />}
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
                </AccountPanel>
              );
            })}
          </div>
        )}
        {err && <p className="error">{err}</p>}
      </Card>

      {manage && (
        <Card title={`Todas las cuentas · ${accounts.length} conocidas, ${connectedCount} conectadas, ${enabledCount} activas`}>
          <p className="muted small">Todo lo que NinjaTrader conoce, conectado o no. <b>Ocultar</b> una cuenta la quita de Inicio, Cuentas, Copiar y de las cuentas en juego, y nunca recibe copias; <b>Mostrar</b> la devuelve. Por defecto una cuenta se muestra <b>mientras está conectada</b> y se oculta al desconectarse (modo <i>auto</i>); si la ocultas o muestras a mano queda fijada, y con <i>auto</i> vuelve a la política automática.</p>
          <div className="manage-bar">
            <input placeholder="Buscar cuenta, alias o conexión…" value={q} onChange={(e) => setQ(e.target.value)} />
            <div className="chips">
              {([["connected", `Conectadas (${connectedCount})`], ["enabled", `Visibles (${enabledCount})`], ["hidden", `Ocultas (${hidden})`], ["all", `Todas (${accounts.length})`]] as const).map(([k, l]) => (
                <button key={k} className={`chip-btn ${filter === k ? "active" : ""}`} onClick={() => setFilter(k)}>{l}</button>))}
            </div>
          </div>
          <div className="manage-bar bulk-bar">
            <span className="muted small">{msel.size ? `${msel.size} marcadas` : "Marca cuentas para actuar sobre varias a la vez"}</span>
            {msel.size > 0 && <div className="chips">
              <button className="chip-btn" disabled={busy === "__all"} onClick={() => void linkMany([...msel].filter((id) => accounts.find((a) => a.account_id === id)?.enabled), "Marcadas")} title="Solo las visibles">Vincular marcadas</button>
              <button className="chip-btn" disabled={busy === "__bulk"} onClick={() => void bulkAccounts("enable")}>Mostrar marcadas</button>
              <button className="chip-btn" disabled={busy === "__bulk"} onClick={() => void bulkAccounts("disable")} data-testid="hide-selected">Ocultar marcadas</button>
              <button className="chip-btn" disabled={busy === "__bulk"} onClick={() => void bulkAccounts("auto")}>Modo auto</button>
              <button className="chip-btn danger" disabled={busy === "__bulk"} onClick={() => void bulkAccounts("forget")} title="Solo las que NinjaTrader ya no reporta">Olvidar marcadas</button>
              <button className="chip-btn" onClick={() => setMsel(new Set())}>Desmarcar</button>
            </div>}
          </div>
          {managed.length === 0 && <Empty>Nada que mostrar con este filtro.</Empty>}
          <div className="table-wrap"><table className="manage">
            <thead><tr><th><input type="checkbox" title="Marcar las visibles" checked={managed.length > 0 && managed.slice(0, 300).every((a) => msel.has(a.account_id))}
              onChange={(e) => setMsel(e.target.checked ? new Set(managed.slice(0, 300).map((a) => a.account_id)) : new Set())} /></th><th>Visible</th><th>Cuenta</th><th>Alias</th><th>Conexión</th><th className="num">Saldo</th><th>Vista</th><th></th></tr></thead>
            <tbody>{managed.slice(0, 300).map((a) => (
              <tr key={a.account_id} className={`${a.enabled ? "" : "off"} ${msel.has(a.account_id) ? "selected" : ""}`}>
                <td><input type="checkbox" checked={msel.has(a.account_id)} onChange={() => toggleMsel(a.account_id)} /></td>
                <td><div className="act-cell"><label className="switch small"><input type="checkbox" checked={a.enabled} disabled={busy === a.account_id || a.account_id === master}
                  onChange={(e) => void setEnabled(a.account_id, e.target.checked)} /><span /></label>
                  {a.enabled_source === "user" ? <button className="link tiny" title="Volver a automático" onClick={() => void setAuto(a.account_id)}>fijada · auto</button> : <span className="muted tiny">auto</span>}</div></td>
                <td><b>{a.account_id}</b>{a.account_id === master && <span className="badge ok">maestra</span>}</td>
                <td><input className="alias" defaultValue={a.alias} placeholder="p. ej. Eval MFF 50k" maxLength={40}
                  onBlur={(e) => { if (e.target.value !== a.alias) void setAlias(a.account_id, e.target.value); }} /></td>
                <td><ConnBadge a={a} /></td>
                <td className="num">{money(a.balance)}</td>
                <td className="muted nowrap">{ago(a.updated_at)}</td>
                <td className="nowrap">{a.account_id !== master && <button className="ghost small-btn" disabled={busy === a.account_id} onClick={() => void setEnabled(a.account_id, !a.enabled)} data-testid={`hide-${a.account_id}`}>{a.enabled ? "Ocultar" : "Mostrar"}</button>}
                  {!a.reported && <button className="ghost danger small-btn" onClick={() => void forget(a.account_id)}>Olvidar</button>}</td>
              </tr>
            ))}</tbody>
          </table></div>
          {managed.length > 300 && <p className="muted small">Mostrando 300 de {managed.length}. Afina la búsqueda.</p>}
        </Card>
      )}
    </div>
  );
}
