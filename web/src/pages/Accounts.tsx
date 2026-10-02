import { useMemo, useState, type FormEvent } from "react";
import { useStore } from "../lib/store";
import { money, signedMoney } from "../lib/format";
import { Card, Empty, Kpi } from "../components/ui";
import { AccountPanel, livePnl, liveDrawdown } from "../components/AccountPanel";
import { ProfileEditor, type ProfileResult } from "../components/ProfileEditor";
import { DD_SHORT, profileLabel } from "../lib/propfirms";
import { Icon } from "../components/Icons";
import type { Account, ExecOptions, Rule } from "../lib/api";

/* Cuentas: UNA tabla con todas las cuentas (maestra y seguidoras) y todo lo que se puede hacer con ellas: copiar o no,
 * multiplicador, corte del día, objetivo de la evaluación, drawdown, posición, prop firm, opciones de ejecución, cerrar,
 * igualar, ocultar. Las acciones en lote actúan sobre las marcadas. Cada fila se despliega para ver el panel completo.
 * Las reglas con filtro de símbolo (copiar solo NQ, por ejemplo) van en una tarjeta aparte al final. */

type Filter = "copying" | "visible" | "hidden" | "all";
type Sort = "default" | "pnl" | "name" | "dd" | "goal";

const label = (a: Account) => a.alias || a.account_id;
const pct = (v: number, of: number) => (of > 0 ? Math.min(100, Math.max(0, (v / of) * 100)) : null);

/** Barra de progreso compacta para las celdas de corte, objetivo y drawdown. */
function Meter({ value, of, done, invert, text }: { value: number; of: number; done?: boolean; invert?: boolean; text?: string }) {
  const p = pct(value, of);
  if (p === null) return <span className="muted">—</span>;
  const tone = done ? "ok" : invert ? (p >= 80 ? "bad" : p >= 50 ? "warn" : "ok") : p >= 80 ? "warn" : "accent";
  return (
    <div className="cell-meter" title={text}>
      <div className="meter"><i className={tone} style={{ width: `${p}%` }} /></div>
      <span className={`small ${done ? "ok" : "muted"}`}>{text ?? `${p.toFixed(0)} %`}</span>
    </div>
  );
}

export function Accounts() {
  const { client, accounts, rules, setRules, risk, setRisk, health, audit, prices } = useStore();
  const detected = health?.bridge.master_account ?? null;
  const master = detected ?? "";
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("visible");
  const [sort, setSort] = useState<Sort>("default");
  const [msel, setMsel] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<string | null>(null);            // fila desplegada
  const [openProfile, setOpenProfile] = useState<string | null>(null);
  const [openOpts, setOpenOpts] = useState<string | null>(null);
  const [profileAlso, setProfileAlso] = useState<string[]>([]);     // preselección al abrir el perfil desde el lote
  const [showRules, setShowRules] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const limits = useMemo(() => new Map((risk?.limits ?? []).map((l) => [l.account_id, l])), [risk]);
  const linkOf = (acc: string): Rule | undefined =>
    rules.find((r) => r.master_account.toLowerCase() === master.toLowerCase() && r.follower_account.toLowerCase() === acc.toLowerCase() && !r.symbol_filter);
  const lastFill = (acc: string) => audit.find((a) => a.event_type === "FOLLOWER_FILL" && a.target_account === acc);
  const lastReject = (acc: string) => audit.find((a) => a.event_type === "FOLLOWER_REJECTED" && a.target_account === acc);
  const isMaster = (a: Account) => a.account_id.toLowerCase() === master.toLowerCase();

  async function guard(acc: string, fn: () => Promise<void>, done?: string) {
    setBusy(acc); setErr(null); setMsg(null);
    try { await fn(); if (done) setMsg(done); } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  }
  /** Acción sobre varias cuentas: una llamada por cuenta, errores agrupados. */
  async function each(ids: string[], fn: (id: string) => Promise<void>): Promise<string[]> {
    const errors: string[] = [];
    for (const id of ids) { try { await fn(id); } catch (ex) { errors.push(`${id}: ${ex instanceof Error ? ex.message : String(ex)}`); } }
    return errors;
  }
  const refreshRisk = async () => setRisk(await client!.risk());

  // ---- copia ----
  const apply = (acc: string, enabled: boolean, multiplier: number, opts: ExecOptions = {}) => guard(acc, async () => {
    const r = await client!.link(acc, master, multiplier, enabled, opts); setRules([...rules.filter((x) => x.id !== r.id), r]);
  });
  const remove = (acc: string) => guard(acc, async () => { const id = linkOf(acc)?.id; await client!.unlink(acc, master); setRules(rules.filter((r) => r.id !== id)); });
  const linkMany = (ids: string[]) => guard("__bulk", async () => {
    if (!master) throw new Error("No hay cuenta maestra detectada.");
    const targets = ids.filter((id) => id.toLowerCase() !== master.toLowerCase() && !linkOf(id)?.enabled && accounts.find((a) => a.account_id === id)?.enabled);
    if (!targets.length) { setMsg(`Ya copian todas a ${master}.`); return; }
    const v = prompt(`Vincular ${targets.length} cuentas a ${master}.\n\nMultiplicador para las que no tienen regla (las que ya la tienen conservan el suyo):`, "1");
    if (v === null) return;
    const m = Number(v.replace(",", "."));
    if (!(m > 0)) throw new Error("Multiplicador no válido.");
    let next = rules;
    const errors = await each(targets, async (id) => { const prev = linkOf(id); const r = await client!.link(id, master, prev?.multiplier ?? m, true); next = [...next.filter((x) => x.id !== r.id), r]; });
    setRules(next); setMsel(new Set());
    if (errors.length) throw new Error(errors.join("; "));
  }, "Cuentas vinculadas.");
  const setCopyMany = (ids: string[], enabled: boolean) => guard("__bulk", async () => {
    const targets = ids.filter((id) => linkOf(id) && linkOf(id)!.enabled !== enabled);
    if (!targets.length) { setMsg("Nada que cambiar."); return; }
    let next = rules;
    const errors = await each(targets, async (id) => { const r = await client!.updateRule(linkOf(id)!.id, { enabled }); next = next.map((x) => (x.id === r.id ? r : x)); });
    setRules(next);
    if (errors.length) throw new Error(errors.join("; "));
  }, enabled ? "Copia reanudada en las marcadas." : "Copia pausada en las marcadas (no se cierran posiciones).");
  const pauseAll = () => guard("__kill", async () => {
    const active = !risk?.kill_switch;
    if (active && !confirm("¿Pausar la copia de TODAS las cuentas? No se cierra ninguna posición; ninguna entrada nueva se replica hasta reanudar.")) return;
    setRisk(await client!.killSwitch(active, active ? "pausa desde Cuentas" : undefined, false, false));
  });

  // ---- límites: corte del día, objetivo, perfil ----
  const limitBody = (acc: string, patch: Partial<{ max_daily_profit: number; profit_goal: number; start_balance: number }>) => {
    const l = limits.get(acc);
    const body = { account_id: acc, max_daily_loss: l?.max_daily_loss ?? 0, max_daily_profit: l?.max_daily_profit ?? 0, profit_goal: l?.profit_goal ?? 0, start_balance: l?.start_balance ?? 0,
                   max_position_size: l?.max_position_size ?? 0, max_trailing_drawdown: l?.max_trailing_drawdown ?? 0, drawdown_mode: l?.drawdown_mode ?? "intraday" as const,
                   drawdown_floor_cap: l?.drawdown_floor_cap ?? 0, drawdown_buffer: l?.drawdown_buffer ?? 0, ...patch };
    const cleared = ("max_daily_profit" in patch && l?.halted_reason === "daily_profit") || ("profit_goal" in patch && l?.halted_reason === "profit_goal");
    return { ...body, trading_halted: !!l?.trading_halted && !cleared };
  };
  const startOf = (a: Account) => limits.get(a.account_id)?.start_balance || a.plan_size || Math.round(a.balance - a.net_pnl);
  const setTarget = (acc: string, target: number) => guard(acc, async () => { await client!.upsertLimit(limitBody(acc, { max_daily_profit: target })); await refreshRisk(); });
  const setGoal = (acc: string, goal: number, start: number) => guard(acc, async () => {
    if (goal > 0 && !(start > 0)) { const v = prompt(`Saldo inicial de ${acc} (desde dónde se cuenta el objetivo):`, String(Math.round(accounts.find((a) => a.account_id === acc)?.balance ?? 0))); if (v === null) return; start = Number(v.replace(",", ".")) || 0; if (!(start > 0)) throw new Error("Saldo inicial no válido."); }
    await client!.upsertLimit(limitBody(acc, { profit_goal: goal, start_balance: goal > 0 ? start : (limits.get(acc)?.start_balance ?? 0) })); await refreshRisk();
  });
  const setTargetMany = (ids: string[]) => guard("__bulk", async () => {
    if (!ids.length) return;
    const v = prompt(`Corte de ganancias del día (NETO, descontadas comisiones) para ${ids.length} cuentas (0 = sin corte). Al alcanzarlo, el engine cierra la posición de esa cuenta y deja de copiarle hasta mañana:`, String(limits.get(ids[0])?.max_daily_profit || ""));
    if (v === null) return;
    const target = Math.max(0, Number(v.replace(",", ".")) || 0);
    const errors = await each(ids, async (id) => { await client!.upsertLimit(limitBody(id, { max_daily_profit: target })); });
    await refreshRisk();
    if (errors.length) throw new Error(errors.join("; "));
  }, "Corte del día aplicado.");
  const setGoalMany = (ids: string[]) => guard("__bulk", async () => {
    if (!ids.length) return;
    const v = prompt(`Objetivo de la evaluación para ${ids.length} cuentas (ganancia neta acumulada sobre el saldo inicial; 0 = sin objetivo). Al alcanzarlo, el engine cierra y deja de copiar a esa cuenta:`, String(limits.get(ids[0])?.profit_goal || ""));
    if (v === null) return;
    const goal = Math.max(0, Number(v.replace(",", ".")) || 0);
    const errors = await each(ids, async (id) => { const a = accounts.find((x) => x.account_id === id)!; await client!.upsertLimit(limitBody(id, { profit_goal: goal, start_balance: goal > 0 ? startOf(a) : (limits.get(id)?.start_balance ?? 0) })); });
    await refreshRisk();
    if (errors.length) throw new Error(errors.join("; "));
  }, "Objetivo aplicado.");
  const applyProfile = async (acc: string, r: ProfileResult, alsoTo: string[]) => {
    setBusy(acc); setErr(null);
    try {
      const errors = await each([acc, ...alsoTo], async (id) => {
        await client!.setAccount(id, { firm: r.firm, plan: r.plan, plan_size: r.size });
        if (r.firm) {
          const l = limits.get(id);
          await client!.upsertLimit({ account_id: id, max_daily_loss: r.dailyLoss, max_daily_profit: r.dayCut, profit_goal: r.target, start_balance: r.target > 0 ? r.size : (l?.start_balance ?? 0), max_position_size: r.contracts,
                                      max_trailing_drawdown: r.drawdown, drawdown_mode: r.dd, drawdown_floor_cap: r.cap, drawdown_buffer: r.buffer,
                                      trading_halted: !!l?.trading_halted && !!l.halted_reason && l.halted_reason !== "daily_profit" });
        }
      });
      await refreshRisk(); setMsel(new Set()); setProfileAlso([]);
      if (errors.length) throw new Error(errors.join("; "));
      setMsg(`Perfil aplicado a ${1 + alsoTo.length} ${alsoTo.length ? "cuentas" : "cuenta"}.`);
    } finally { setBusy(null); }
  };

  // ---- cuenta ----
  const setEnabled = (acc: string, enabled: boolean) => guard(acc, async () => { await client!.setAccount(acc, { enabled }); });
  const setAuto = (acc: string) => guard(acc, async () => { await client!.setAccount(acc, { auto: true }); });
  const setAlias = (acc: string, alias: string) => guard(acc, async () => { await client!.setAccount(acc, { alias }); });
  const changeMaster = (acc: string) => guard("__master", async () => {
    if (!acc || acc === detected) return;
    if (!confirm(`¿Hacer líder a ${acc}?\n\nA partir de ahora se copiarán las operaciones de ${acc}. Las seguidoras de ${detected ?? "la líder anterior"} dejan de copiar hasta que las vincules a la nueva (botón "Vincular todas").`)) return;
    await client!.setMaster(acc);
  });
  const flatten = (acc: string) => guard(acc, async () => {
    if (!confirm(`¿CERRAR ${acc}?\n\nSe cancelan todas sus órdenes y se cierra la posición a mercado.`)) return;
    await client!.flatten(acc, "manual desde Cuentas");
  });
  const flattenMany = (ids: string[]) => guard("__bulk", async () => {
    const targets = ids.filter((id) => accounts.find((a) => a.account_id === id)?.open_positions.length);
    if (!targets.length) { setMsg("Ninguna de las marcadas tiene posición abierta."); return; }
    if (!confirm(`¿CERRAR ${targets.length} cuentas a mercado?\n\n${targets.join(", ")}`)) return;
    const errors = await each(targets, async (id) => { await client!.flatten(id, "manual desde Cuentas (lote)"); });
    if (errors.length) throw new Error(errors.join("; "));
  }, "Posiciones cerradas.");
  const resync = (acc: string) => guard(acc, async () => {
    if (!confirm(`¿Igualar ${acc} a la maestra?\n\nSe manda a mercado la diferencia de contratos.`)) return;
    const r = await client!.resync(acc);
    if (r.sent.length === 0) setMsg("Ya coincide con la maestra.");
  });
  const forget = (acc: string) => guard(acc, async () => { if (confirm(`¿Olvidar ${acc}? Volverá a aparecer si NinjaTrader la reporta.`)) await client!.forgetAccount(acc); });
  const bulkAccounts = (action: "enable" | "disable" | "auto" | "forget") => guard("__bulk", async () => {
    const ids = [...msel].filter((id) => id !== master && (action !== "forget" || !accounts.find((a) => a.account_id === id)?.reported));
    if (!ids.length) { setMsg(action === "forget" ? "Solo se pueden olvidar cuentas que NinjaTrader ya no reporta." : "Nada que hacer con las marcadas."); return; }
    const ask = { enable: `¿Mostrar ${ids.length} cuentas? Vuelven a la lista y pueden recibir copias.`, disable: `¿Ocultar ${ids.length} cuentas? Desaparecen de Inicio y de esta lista (filtro Ocultas) y nunca reciben copias.`,
                  auto: `¿Volver a modo automático ${ids.length} cuentas? (visible = conectada)`, forget: `¿Olvidar ${ids.length} cuentas que NinjaTrader ya no reporta?` }[action];
    if (!confirm(`${ask}\n\n${ids.slice(0, 12).join(", ")}${ids.length > 12 ? `… (+${ids.length - 12})` : ""}`)) return;
    const errors = await each(ids, async (id) => {
      if (action === "forget") await client!.forgetAccount(id);
      else if (action === "auto") await client!.setAccount(id, { auto: true });
      else await client!.setAccount(id, { enabled: action === "enable" });
    });
    setMsel(new Set());
    if (errors.length) throw new Error(errors.join("; "));
  });
  const toggleMsel = (id: string) => setMsel((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  // ---- filas ----
  const rows = useMemo(() => accounts.map((a) => {
    const link = isMaster(a) ? undefined : linkOf(a.account_id);
    const limit = limits.get(a.account_id);
    const gross = livePnl(a, prices); const fees = a.commissions_today || 0;
    const pnl = gross.value - fees;
    const dd = liveDrawdown(a, prices);
    const start = limit?.start_balance || a.plan_size || 0;
    const gained = start > 0 ? a.balance + a.unrealized_pnl - fees - start : null;
    const copying = !!link?.enabled && !limit?.trading_halted && !risk?.kill_switch;
    return { a, link, limit, pnl, live: gross.live, dd, start, gained, copying };
  }), [accounts, rules, limits, prices, master, risk?.kill_switch]);   // eslint-disable-line react-hooks/exhaustive-deps
  const needle = q.trim().toLowerCase();
  const visible = rows.filter(({ a, link }) => {
    if (needle && !a.account_id.toLowerCase().includes(needle) && !a.alias.toLowerCase().includes(needle) && !a.firm.toLowerCase().includes(needle)) return false;
    if (filter === "all") return true;
    if (filter === "hidden") return !a.enabled;
    if (filter === "copying") return isMaster(a) || !!link?.enabled;
    return a.enabled;
  }).sort((x, y) => {
    if (isMaster(x.a) !== isMaster(y.a)) return isMaster(x.a) ? -1 : 1;
    if (sort === "pnl") return y.pnl - x.pnl;
    if (sort === "name") return label(x.a).localeCompare(label(y.a));
    if (sort === "dd") return (y.dd.pct ?? -1) - (x.dd.pct ?? -1);
    if (sort === "goal") return ((y.limit?.profit_goal ? (y.gained ?? 0) / y.limit.profit_goal : -1) - (x.limit?.profit_goal ? (x.gained ?? 0) / x.limit.profit_goal : -1));
    const r = (z: typeof x) => (z.copying ? 0 : z.link ? 1 : z.a.enabled ? 2 : 3);
    return r(x) - r(y) || label(x.a).localeCompare(label(y.a));
  });
  const followers = rows.filter(({ a }) => !isMaster(a) && a.enabled);
  const counts = { copying: followers.filter((r) => r.copying).length, paused: followers.filter((r) => r.link && (!r.link.enabled || r.limit?.trading_halted)).length,
                   desync: followers.filter((r) => r.a.desync).length, hidden: rows.filter((r) => !r.a.enabled).length, visible: rows.filter((r) => r.a.enabled).length,
                   pnl: followers.reduce((s, r) => s + r.pnl, 0), open: followers.reduce((s, r) => s + r.a.open_positions.length, 0), copyingAll: rows.filter((r) => r.copying).length };
  const selIds = [...msel].filter((id) => id !== master);
  const selVisible = visible.length > 0 && visible.every((r) => msel.has(r.a.account_id));
  const masterRow = rows.find((r) => isMaster(r.a));
  if (!client) return null;

  return (
    <div className="grid" data-testid="accounts">
      <Card title="Cuenta líder y copia" icon="crown" right={<div className="chips">
        <button className={risk?.kill_switch ? "primary small-btn" : "small-btn"} disabled={busy === "__kill"} onClick={() => void pauseAll()} data-testid="pause-all" title="Kill switch sin cerrar posiciones">{risk?.kill_switch ? "Reanudar copia" : "Pausar toda la copia"}</button>
        <button className="primary small-btn" disabled={busy === "__bulk" || !master} onClick={() => void linkMany(followers.map((r) => r.a.account_id))} data-testid="link-all">Vincular todas</button>
        <button className="small-btn" disabled={busy === "__bulk"} onClick={() => void setTargetMany(followers.map((r) => r.a.account_id))} data-testid="target-all">Corte para todas</button>
        <button className="small-btn" disabled={busy === "__bulk"} onClick={() => void setGoalMany(followers.map((r) => r.a.account_id))} data-testid="goal-all">Objetivo para todas</button>
      </div>}>
        <div className="master-row">
          <label className="leader-pick" title="La cuenta líder es la que se copia: sus operaciones se replican en las demás">
            <span className="crown-badge"><Icon name="crown" size={16} /></span>
            <span className="small muted">Líder</span>
            <select value={master} disabled={busy === "__master"} onChange={(e) => void changeMaster(e.target.value)} data-testid="master-select">
              {!master && <option value="">Elige la cuenta líder…</option>}
              {accounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.account_id === detected ? "👑 " : ""}{label(a)}{a.alias ? ` · ${a.account_id}` : ""}</option>)}
            </select>
          </label>
          {masterRow && <div className="master-summary">
            <span className={`badge ${masterRow.a.connected ? "ok" : "bad"}`}>{masterRow.a.connected ? "conectada" : "desconectada"}</span>
            <span>P&L hoy <b className={masterRow.pnl > 0 ? "ok" : masterRow.pnl < 0 ? "bad" : ""}>{masterRow.live && "≈ "}{signedMoney(masterRow.pnl)}</b></span>
            <span>{masterRow.a.open_positions.length ? masterRow.a.open_positions.map((p) => `${p.quantity > 0 ? "largo" : "corto"} ${Math.abs(p.quantity)} ${p.symbol}`).join(" · ") : "plana"}</span>
            <span className="muted">{money(masterRow.a.balance)}</span>
          </div>}
        </div>
        <p className="muted small">La <b>líder</b> es la cuenta que operas: todo lo que haga se copia en las seguidoras que tengan el interruptor activado. Para cambiarla elige otra en el desplegable o pulsa <b>Hacer líder</b> en su fila. El addon la guarda y sobrevive a reinicios (addon v1.2 o superior).</p>
        {risk?.kill_switch && <p className="error">Copia detenida (kill switch){risk.kill_switch_reason ? ` · ${risk.kill_switch_reason}` : ""}: ninguna cuenta copia hasta reanudar.</p>}
        {err && busy === null && <p className="error" data-testid="acc-error">{err}</p>}
        {msg && !err && <p className="ok small" data-testid="acc-msg">{msg}</p>}
      </Card>

      <div className="kpis an-kpis acc-kpis" data-testid="acc-kpis">
        <Kpi label="Copiando" icon="copy" value={`${counts.copying} / ${followers.length}`} tone={counts.copying ? "ok" : "muted"} ring={followers.length ? (100 * counts.copying) / followers.length : null} sub="seguidoras visibles" />
        <Kpi label="Pausadas" icon="x" value={counts.paused} tone={counts.paused ? "warn" : "muted"} sub="por ti o por un límite" />
        <Kpi label="Desincronizadas" icon="bolt" value={counts.desync} tone={counts.desync ? "bad" : "ok"} sub="no coinciden con la maestra" />
        <Kpi label="P&L seguidoras hoy" icon="wallet" value={signedMoney(counts.pnl)} tone={counts.pnl > 0 ? "ok" : counts.pnl < 0 ? "bad" : "muted"} sub={`neto · ${counts.open} posiciones abiertas · ${counts.hidden} ocultas`} />
      </div>

      <Card title={`Cuentas · ${visible.length}`} icon="list" right={<div className="chips acc-filters">
        <input placeholder="Buscar cuenta, alias o prop firm…" value={q} onChange={(e) => setQ(e.target.value)} data-testid="acc-search" />
        <div className="view-tabs" data-testid="acc-filter">
          {([["copying", `Copiando (${counts.copyingAll})`], ["visible", `Visibles (${counts.visible})`], ["hidden", `Ocultas (${counts.hidden})`], ["all", `Todas (${rows.length})`]] as const).map(([k, l]) => <button key={k} className={filter === k ? "active" : ""} onClick={() => setFilter(k)}>{l}</button>)}
        </div>
        <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} data-testid="acc-sort"><option value="default">Orden: estado</option><option value="pnl">Orden: P&L hoy</option><option value="goal">Orden: % objetivo</option><option value="dd">Orden: drawdown</option><option value="name">Orden: nombre</option></select>
      </div>}>
        <div className="manage-bar bulk-bar">
          <span className="muted small">{selIds.length ? `${selIds.length} marcadas` : "Marca cuentas para actuar sobre varias a la vez · pulsa ▾ para ver el detalle de una cuenta"}</span>
          {selIds.length > 0 && <div className="chips" data-testid="bulk-bar">
            <button className="chip-btn" disabled={!!busy} onClick={() => void linkMany(selIds)}>Vincular</button>
            <button className="chip-btn" disabled={!!busy} onClick={() => void setCopyMany(selIds, false)} data-testid="bulk-pause">Pausar copia</button>
            <button className="chip-btn" disabled={!!busy} onClick={() => void setCopyMany(selIds, true)} data-testid="bulk-resume">Reanudar copia</button>
            <button className="chip-btn" disabled={!!busy} onClick={() => void setTargetMany(selIds)}>Corte del día…</button>
            <button className="chip-btn" disabled={!!busy} onClick={() => void setGoalMany(selIds)}>Objetivo…</button>
            <button className="chip-btn" disabled={!!busy} onClick={() => { setProfileAlso(selIds.slice(1)); setOpen(selIds[0]); setOpenProfile(selIds[0]); setOpenOpts(null); }} data-testid="bulk-profile">Prop firm…</button>
            <button className="chip-btn danger" disabled={!!busy} onClick={() => void flattenMany(selIds)}>Cerrar posiciones</button>
            <button className="chip-btn" disabled={!!busy} onClick={() => void bulkAccounts("disable")} data-testid="hide-selected">Ocultar</button>
            <button className="chip-btn" disabled={!!busy} onClick={() => void bulkAccounts("enable")}>Mostrar</button>
            <button className="chip-btn" disabled={!!busy} onClick={() => void bulkAccounts("auto")} title="Visible mientras esté conectada">Modo auto</button>
            <button className="chip-btn danger" disabled={!!busy} onClick={() => void bulkAccounts("forget")} title="Solo las que NinjaTrader ya no reporta">Olvidar</button>
            <button className="chip-btn" onClick={() => setMsel(new Set())}>Desmarcar</button>
          </div>}
        </div>
        {visible.length === 0 ? <Empty>{accounts.length ? "Nada que mostrar con este filtro." : "Esperando cuentas del bróker…"}</Empty> : (
          <div className="table-wrap">
            <table className="acc-table" data-testid="acc-table">
              <thead><tr>
                <th><input type="checkbox" title="Marcar las visibles" checked={selVisible} onChange={(e) => setMsel(e.target.checked ? new Set(visible.map((r) => r.a.account_id)) : new Set())} /></th>
                <th>Cuenta</th><th>Estado</th><th>Copia</th><th className="num">P&L hoy</th><th>Corte del día</th><th>Objetivo evaluación</th><th>Drawdown</th><th>Posición</th><th></th>
              </tr></thead>
              <tbody>
                {visible.map(({ a, link, limit, pnl, live, dd, start, gained, copying }) => {
                  const id = a.account_id; const m = isMaster(a); const expanded = open === id;
                  const halted = limit?.trading_halted ? limit.halted_reason : "";
                  const prof = profileLabel(a.firm, a.plan, a.plan_size);
                  return [
                    <tr key={id} className={`${!a.enabled ? "off" : ""} ${msel.has(id) ? "selected" : ""} ${expanded ? "open" : ""} ${a.desync ? "row-desync" : ""} ${m ? "leader" : ""}`} data-account={id} data-testid={`row-${id}`}>
                      <td><input type="checkbox" checked={msel.has(id)} onChange={() => toggleMsel(id)} /></td>
                      <td><div className="acc-cell">
                        <b>{m && <span className="crown-badge inline" title="Cuenta líder"><Icon name="crown" size={13} /></span>}{label(a)}</b>{a.alias && <span className="muted small">{id}</span>}
                        {prof && <span className="chip profile small">{prof} · {DD_SHORT[dd.mode === "static" ? "static" : dd.mode === "eod" ? "eod" : "intraday"]}</span>}
                      </div></td>
                      <td><div className="status-cell">
                        {m ? <span className="badge accent leader-badge"><Icon name="crown" size={11} /> líder</span> : null}
                        {!a.enabled ? <span className="badge muted">oculta</span> : a.connected === false ? <span className="badge bad">desconectada</span> : !a.reported ? <span className="badge muted">no reportada</span> : null}
                        {a.desync && <span className="badge bad">desincronizada</span>}
                        {halted && <span className={`badge ${halted === "daily_profit" || halted === "profit_goal" ? "ok" : "bad"}`}>{{ daily_profit: "corte hecho", profit_goal: "evaluación superada", daily_loss: "pérdida diaria", drawdown: "drawdown" }[halted] ?? "pausada"}</span>}
                        {!m && a.enabled && !halted && (link ? (link.enabled ? (risk?.kill_switch ? <span className="badge warn">kill switch</span> : <span className="badge ok">copiando</span>) : <span className="badge warn">pausada</span>) : <span className="badge muted">sin regla</span>)}
                      </div></td>
                      <td><div className="copy-cell">
                        {m ? <span className="muted small">se copia</span> : <>
                          <label className="switch small" title={link?.enabled ? "Dejar de copiar" : "Copiar a la maestra"}>
                            <input type="checkbox" checked={!!link?.enabled} disabled={busy === id || !master || !a.enabled} onChange={(e) => void apply(id, e.target.checked, link?.multiplier ?? 1)} data-testid={`copy-${id}`} /><span />
                          </label>
                          <label className="mult">×<input key={`m-${link?.multiplier ?? 1}`} type="number" step="0.1" min="0.1" defaultValue={link?.multiplier ?? 1} disabled={busy === id || !master} data-testid={`mult-${id}`}
                            onBlur={(e) => { const v = Number(e.target.value); if (v > 0 && v !== (link?.multiplier ?? 1)) void apply(id, link?.enabled ?? false, v); }} /></label>
                          {link?.target_root && <span className="chip">→ {link.target_root}</span>}
                        </>}
                      </div></td>
                      <td className={`num ${pnl > 0 ? "ok" : pnl < 0 ? "bad" : ""}`}>{live && "≈ "}{signedMoney(pnl)}</td>
                      <td>{limit?.max_daily_profit ? <Meter value={pnl} of={limit.max_daily_profit} done={halted === "daily_profit"} text={halted === "daily_profit" ? "hecho" : `${(pct(pnl, limit.max_daily_profit) ?? 0).toFixed(0)} % de ${money(limit.max_daily_profit)}`} /> : <span className="muted">—</span>}</td>
                      <td>{limit?.profit_goal && gained !== null ? <Meter value={gained} of={limit.profit_goal} done={halted === "profit_goal"} text={halted === "profit_goal" ? "superada" : `${signedMoney(gained)} de ${money(limit.profit_goal)} (desde ${money(start)})`} /> : <span className="muted">—</span>}</td>
                      <td>{dd.pct !== null ? <Meter value={dd.pct} of={100} invert text={`${dd.pct.toFixed(0)} % · quedan ${money(dd.room ?? 0)}`} /> : <span className={`small ${dd.drawdown > 0 ? "bad" : "muted"}`}>{dd.drawdown > 0 ? `−${money(dd.drawdown)}` : "0"}</span>}</td>
                      <td><div className="pos-cell">{a.open_positions.length ? a.open_positions.map((p) => <span key={p.symbol} className={`small ${p.quantity > 0 ? "ok" : "bad"}`}>{p.quantity > 0 ? "L" : "C"} {Math.abs(p.quantity)} {p.symbol.split(" ")[0]}</span>) : <span className="muted small">plana</span>}</div></td>
                      <td className="nowrap"><div className="row-actions">
                        {!m && a.enabled && <button className="ghost small-btn leader-btn" disabled={busy === "__master"} onClick={() => void changeMaster(id)} title="Convertir esta cuenta en la líder (la que se copia)" data-testid={`lead-${id}`}><Icon name="crown" size={12} /> Hacer líder</button>}
                        {a.desync && <button className="ghost small-btn" disabled={busy === id} onClick={() => void resync(id)}>Igualar</button>}
                        {a.open_positions.length > 0 && <button className="danger small-btn" disabled={busy === id} onClick={() => void flatten(id)}>Cerrar</button>}
                        <button className={`ghost small-btn ${expanded ? "active" : ""}`} onClick={() => { setOpen(expanded ? null : id); setOpenProfile(null); setOpenOpts(null); setProfileAlso([]); }} data-testid={`expand-${id}`} aria-expanded={expanded} title="Detalle">{expanded ? "▴" : "▾"}</button>
                      </div></td>
                    </tr>,
                    expanded && (
                      <tr key={`${id}-x`} className="expand-row"><td colSpan={10}>
                        <div className="expand-head">
                          <div className="chips">
                            <button className={`ghost small-btn ${openProfile === id ? "active" : ""}`} onClick={() => { setOpenProfile(openProfile === id ? null : id); setOpenOpts(null); }} data-testid={`profile-${id}`}>Prop firm</button>
                            {!m && <button className={`ghost small-btn ${openOpts === id ? "active" : ""}`} onClick={() => { setOpenOpts(openOpts === id ? null : id); setOpenProfile(null); }} data-testid={`opts-${id}`}>Opciones de ejecución</button>}
                            {!m && link && <button className="ghost danger small-btn" disabled={busy === id} onClick={() => void remove(id)}>Quitar regla</button>}
                            {!m && <button className="ghost small-btn" disabled={busy === id} onClick={() => void setEnabled(id, !a.enabled)} data-testid={`hide-${id}`}>{a.enabled ? "Ocultar" : "Mostrar"}</button>}
                            {a.enabled_source === "user" && <button className="link tiny" onClick={() => void setAuto(id)}>fijada a mano · volver a auto</button>}
                            {!a.reported && <button className="ghost danger small-btn" onClick={() => void forget(id)}>Olvidar</button>}
                          </div>
                          <label className="inline small">Alias <input className="alias" defaultValue={a.alias} placeholder="p. ej. Eval MFF 50k" maxLength={40} onBlur={(e) => { if (e.target.value !== a.alias) void setAlias(id, e.target.value); }} /></label>
                        </div>
                        {openProfile === id && <ProfileEditor a={a} limit={limit} followers={followers.filter((r) => r.a.account_id !== id).map((r) => r.a)} preselect={profileAlso} busy={busy === id}
                                                              onApply={(r, also) => applyProfile(id, r, also)} onClose={() => { setOpenProfile(null); setProfileAlso([]); }} />}
                        {openOpts === id && !m && (
                          <div className="exec-opts">
                            <label>Símbolo destino<input placeholder="igual que la maestra · ej. MNQ" defaultValue={link?.target_root ?? ""} onBlur={(e) => void apply(id, link?.enabled ?? false, link?.multiplier ?? 1, { target_root: e.target.value.trim() })} /></label>
                            <label>Entrada<select value={link?.entry_mode ?? "market"} onChange={(e) => void apply(id, link?.enabled ?? false, link?.multiplier ?? 1, { entry_mode: e.target.value as "market" | "limit" })}>
                              <option value="market">A mercado (copia inmediata)</option><option value="limit">Límite al precio del maestro ± ticks</option></select></label>
                            {(link?.entry_mode ?? "market") === "limit" && <>
                              <label>Tolerancia (ticks)<input type="number" min="0" max="50" defaultValue={link?.tolerance_ticks ?? 2} onBlur={(e) => void apply(id, link?.enabled ?? false, link?.multiplier ?? 1, { tolerance_ticks: Number(e.target.value) })} /></label>
                              <label>Espera (s)<input type="number" min="1" max="120" defaultValue={link?.entry_timeout_s ?? 5} onBlur={(e) => void apply(id, link?.enabled ?? false, link?.multiplier ?? 1, { entry_timeout_s: Number(e.target.value) })} /></label>
                              <label>Si no se llena<select value={link?.entry_fallback ?? "market"} onChange={(e) => void apply(id, link?.enabled ?? false, link?.multiplier ?? 1, { entry_fallback: e.target.value as "market" | "cancel" })}>
                                <option value="market">A mercado lo que falte</option><option value="cancel">Cancelar (no entrar)</option></select></label>
                            </>}
                            <p className="muted small">Las salidas (stops, take profits y cierres) van siempre a mercado o con su propia orden. Símbolo destino: la seguidora opera ese contrato (p. ej. maestra NQ, seguidora MNQ con multiplicador ×10). Requiere addon v1.7 para entradas límite.</p>
                          </div>
                        )}
                        <AccountPanel a={a} role={m ? "master" : "follower"} link={link} limit={limit} prices={prices} busy={busy === id} lastFill={lastFill(id)} lastReject={lastReject(id)}
                                      onFlatten={() => void flatten(id)} onResync={() => void resync(id)} onTarget={(t) => void setTarget(id, t)} onGoal={(g, s) => void setGoal(id, g, s)} />
                      </td></tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small">Ocultar una cuenta la quita de Inicio y de esta lista (queda en el filtro <i>Ocultas</i>) y nunca recibe copias. Por defecto una cuenta se muestra mientras está conectada y se oculta al desconectarse (modo auto); si la ocultas o muestras a mano queda fijada.</p>
      </Card>

      <Card title={`Reglas con filtro de símbolo${rules.filter((r) => r.symbol_filter).length ? ` · ${rules.filter((r) => r.symbol_filter).length}` : ""}`} icon="layers"
            right={<button className="chip-btn" onClick={() => setShowRules(!showRules)} data-testid="toggle-rules">{showRules ? "Ocultar" : "Mostrar"}</button>}>
        <p className="muted small">Una regla con filtro copia solo ese símbolo (p. ej. únicamente NQ) de una maestra concreta. Las reglas normales (toda la operativa de la maestra) se gestionan en la tabla de arriba.</p>
        {showRules && <SymbolRules />}
      </Card>
    </div>
  );
}

/** Reglas con filtro de símbolo (antes en la pestaña Copiar). */
function SymbolRules() {
  const { client, rules, setRules, accounts } = useStore();
  const [master, setMaster] = useState(""); const [follower, setFollower] = useState(""); const [mult, setMult] = useState("1"); const [symbol, setSymbol] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const filtered = rules.filter((r) => r.symbol_filter);
  async function add(e: FormEvent) {
    e.preventDefault(); setErr(null);
    try { const r = await client!.createRule({ master_account: master, follower_account: follower, multiplier: Number(mult) || 1, symbol_filter: symbol || null, enabled: true }); setRules([...rules, r]); setMaster(""); setFollower(""); setSymbol(""); }
    catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); }
  }
  const toggle = async (id: string, enabled: boolean) => { const r = await client!.updateRule(id, { enabled }); setRules(rules.map((x) => (x.id === id ? r : x))); };
  const remove = async (id: string) => { if (!confirm("¿Eliminar esta regla?")) return; await client!.deleteRule(id); setRules(rules.filter((x) => x.id !== id)); };
  return (
    <>
      <form className="rule-form" onSubmit={add} data-testid="symbol-rule-form">
        <label>Maestra<input list="accts" value={master} onChange={(e) => setMaster(e.target.value)} required /></label>
        <label>Seguidora<input list="accts" value={follower} onChange={(e) => setFollower(e.target.value)} required /></label>
        <label>Multiplicador<input type="number" step="0.1" min="0.1" value={mult} onChange={(e) => setMult(e.target.value)} /></label>
        <label>Símbolo<input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="ej. NQ" required /></label>
        <datalist id="accts">{accounts.map((a) => <option key={a.account_id} value={a.account_id} />)}</datalist>
        <button className="primary">Añadir</button>
      </form>
      {err && <p className="error">{err}</p>}
      {filtered.length === 0 ? <Empty>No hay reglas con filtro de símbolo.</Empty> : (
        <div className="table-wrap"><table className="acc-table">
          <thead><tr><th>Maestra</th><th>Seguidora</th><th>Símbolo</th><th className="num">Multiplicador</th><th>Activa</th><th></th></tr></thead>
          <tbody>{filtered.map((r) => <tr key={r.id} className={r.enabled ? "" : "off"}><td><b>{r.master_account}</b></td><td><b>{r.follower_account}</b></td><td><span className="chip">{r.symbol_filter}</span></td><td className="num">×{r.multiplier}</td>
            <td><label className="switch small"><input type="checkbox" checked={r.enabled} onChange={(e) => void toggle(r.id, e.target.checked)} /><span /></label></td>
            <td className="num"><button className="ghost danger small-btn" onClick={() => void remove(r.id)}>Borrar</button></td></tr>)}</tbody>
        </table></div>
      )}
    </>
  );
}
