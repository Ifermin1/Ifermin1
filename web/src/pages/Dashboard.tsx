import { useState } from "react";
import { useStore } from "../lib/store";
import { ago, time } from "../lib/format";
import { Card, Empty, Kpi } from "../components/ui";
import { AccountPanel } from "../components/AccountPanel";
import { PnlChart } from "../components/PnlChart";
import { ExecutionQuality } from "../components/ExecutionQuality";
import { NewsToday } from "../components/NewsPanel";
import { RecentStats } from "../components/RecentStats";
import type { Route } from "../components/Shell";

/** Inicio = sala de control: alertas, P&L del día, cuentas en juego y estado del puente. */
export function Dashboard({ onRoute }: { onRoute?: (r: Route) => void } = {}) {
  const { client, health, accounts, rules, audit, risk, prices, density, setRules } = useStore();
  const [busy, setBusy] = useState<string | null>(null);
  const b = health?.bridge;
  const master = b?.master_account ?? null;
  const limits = new Map((risk?.limits ?? []).map((l) => [l.account_id, l]));
  const linkOf = (acc: string) => rules.find((r) => master && r.master_account.toLowerCase() === master.toLowerCase() && r.follower_account.toLowerCase() === acc.toLowerCase() && !r.symbol_filter);
  const masterAcc = accounts.find((a) => a.account_id === master);
  const copying = accounts.filter((a) => a.enabled && a.account_id !== master && linkOf(a.account_id)?.enabled);
  const inPlay = [...(masterAcc ? [masterAcc] : []), ...copying];
  const openPositions = inPlay.reduce((s, a) => s + a.open_positions.length, 0);
  const lastFill = (acc: string) => audit.find((a) => a.event_type === "FOLLOWER_FILL" && a.target_account === acc);
  const lastReject = (acc: string) => audit.find((a) => a.event_type === "FOLLOWER_REJECTED" && a.target_account === acc);
  const flatten = async (acc: string) => {
    if (!client || !confirm(`¿CERRAR ${acc}?\n\nSe cancelan todas sus órdenes y se cierra la posición a mercado.`)) return;
    setBusy(acc);
    try { await client.flatten(acc, "manual desde inicio"); } catch (ex) { alert(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  };
  const compact = density === "compact";

  // ---- avisos: una sola tarjeta con todo lo que requiere atención, agrupado y con las cuentas como chips ----
  type Alert = { id: string; tone: "bad" | "warn" | "ok"; title: string; text: string; accounts?: string[]; go?: Route };
  const alerts: Alert[] = [];
  const halted = (why: string) => (risk?.limits ?? []).filter((l) => l.trading_halted && l.halted_reason === why).map((l) => l.account_id);
  if (risk?.kill_switch) alerts.push({ id: "kill", tone: "bad", title: `COPIA DETENIDA (kill switch)${risk.kill_switch_at ? ` desde ${new Date(risk.kill_switch_at).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}` : ""}`,
    text: `${risk.kill_switch_reason ? `${risk.kill_switch_reason}. ` : ""}Ninguna entrada se replica: cada operación de la maestra queda como BLOCKED y las seguidoras se desincronizan. Pulsa Reanudar en la barra de estado.` });
  if (health?.addon_outdated) alerts.push({ id: "addon", tone: "bad", title: "Addon de NinjaTrader desactualizado", text: `${health.bridge.addon_version ? `v${health.bridge.addon_version}` : "sin versión"}; se requiere v${health.min_addon_version}. Las protecciones no funcionan hasta recompilarlo.` });
  if (risk?.addon_silent) alerts.push({ id: "silent", tone: "bad", title: "Sin heartbeat del addon", text: "Más de 15 s sin señal: NinjaTrader no está enviando operaciones. Revisa que esté abierto y el addon cargado." });
  if (health?.stats.seq_gaps) alerts.push({ id: "gaps", tone: "warn", title: `Mensajes perdidos del addon: ${health.stats.seq_gaps}`, text: "Revisa la sincronización de las seguidoras.", go: "accounts" });
  if (accounts.some((a) => a.desync)) alerts.push({ id: "desync", tone: "bad", title: "Seguidoras desincronizadas", text: "No coinciden con la maestra: iguálalas o ciérralas.", accounts: accounts.filter((a) => a.desync).map((a) => a.account_id), go: "accounts" });
  if (risk?.session_closed) alerts.push({ id: "session", tone: "warn", title: `Sesión cerrada por horario (${risk.schedule.flatten_at})`, text: "No se copia nada hasta mañana; en Riesgo puedes reabrir.", go: "risk" });
  if (halted("daily_loss").length) alerts.push({ id: "loss", tone: "bad", title: "Límite de pérdida diaria alcanzado", text: "Cuenta pausada y cerrada; se reanuda a mano en Riesgo.", accounts: halted("daily_loss"), go: "risk" });
  if (halted("drawdown").length) alerts.push({ id: "dd", tone: "bad", title: "Límite de drawdown alcanzado", text: "Pausada y cerrada antes de tocar el suelo del prop firm; se reanuda en Riesgo solo si vuelve a tener margen. Si acabas de aplicar un perfil y no cuadra con el saldo, corrige el tamaño.", accounts: halted("drawdown"), go: "risk" });
  const hot = accounts.filter((a) => a.enabled && a.drawdown.pct !== null && a.drawdown.pct >= 80 && !limits.get(a.account_id)?.trading_halted);
  if (hot.length) alerts.push({ id: "hot", tone: "warn", title: "Drawdown al límite", text: "Cerca del suelo del prop firm: reduce o cierra antes de que el engine lo haga por ti.", accounts: hot.map((a) => `${a.alias || a.account_id} (${a.drawdown.pct!.toFixed(0)} %, quedan ${Math.round(a.drawdown.room ?? 0)} $)`), go: "accounts" });
  if (halted("daily_profit").length) alerts.push({ id: "cut", tone: "ok", title: "Corte del día hecho", text: "Ganancia asegurada: posición cerrada y sin copiar hasta mañana.", accounts: halted("daily_profit"), go: "risk" });
  if (halted("profit_goal").length) alerts.push({ id: "goal", tone: "ok", title: "Objetivo de la evaluación alcanzado", text: "Evaluación superada: cuenta cerrada y pausada. Para seguir operándola sube o quita el objetivo en Riesgo.", accounts: halted("profit_goal"), go: "risk" });
  const [showAll, setShowAll] = useState<Record<string, boolean>>({});

  return (
    <div className="grid">
      {alerts.length > 0 && (
        <Card className={`alerts-card ${alerts.some((a) => a.tone === "bad") ? "has-bad" : ""}`} title={`Avisos · ${alerts.length}`} icon="bell" data-testid="alerts">
          <ul className="alerts" data-testid="alerts-list">
            {alerts.map((al) => {
              const list = al.accounts ?? []; const open = !!showAll[al.id]; const shown = open ? list : list.slice(0, 6);
              return (
                <li key={al.id} className={`alert ${al.tone}`} data-testid={`alert-${al.id}`}>
                  <div className="alert-main">
                    <b>{al.title}{list.length ? ` · ${list.length} ${list.length === 1 ? "cuenta" : "cuentas"}` : ""}</b>
                    <span className="muted">{al.text}</span>
                    {list.length > 0 && <div className="chips">{shown.map((x) => <span key={x} className="chip">{x}</span>)}{list.length > 6 && <button className="link tiny" onClick={() => setShowAll({ ...showAll, [al.id]: !open })}>{open ? "ver menos" : `+${list.length - 6} más`}</button>}</div>}
                  </div>
                  {al.go && <button className="ghost small-btn" onClick={() => onRoute?.(al.go!)}>{al.go === "risk" ? "Ir a Riesgo" : "Ir a Cuentas"}</button>}
                </li>
              );
            })}
          </ul>
        </Card>
      )}

      <div className="stats">
        <Kpi label="Copiando" icon="users" value={`${copying.length} / ${accounts.filter((a) => a.enabled && a.account_id !== master).length}`} tone={copying.length ? "ok" : "muted"}
             ring={accounts.filter((a) => a.enabled && a.account_id !== master).length ? (100 * copying.length) / accounts.filter((a) => a.enabled && a.account_id !== master).length : null} sub="seguidoras activas" />
        <Kpi label="Posiciones" icon="layers" value={openPositions} tone={openPositions ? "warn" : "muted"} sub={openPositions ? "abiertas ahora" : "todo plano"} />
        <Kpi label="Órdenes replicadas" icon="copy" value={health?.stats.orders_out ?? 0} tone="accent" sub="en esta sesión" />
        <Kpi label="Fills seguidoras" icon="check" value={health?.stats.fills ?? 0} tone={health?.stats.fills ? "ok" : "muted"} sub="confirmadas" />
        <Kpi label="Rechazadas · bloq." icon="x" value={`${health?.stats.rejected ?? 0} · ${health?.stats.blocked ?? 0}`} tone={(health?.stats.rejected || health?.stats.blocked) ? "bad" : "muted"} sub="por el bróker · por riesgo" />
        <Kpi label="Errores" icon="bolt" value={health?.stats.errors ?? 0} tone={health?.stats.errors ? "bad" : "ok"} sub="del puente" />
        <Kpi label="Latencia copia" icon="clock" value={health?.stats.latency_ms_avg != null ? `${health.stats.latency_ms_avg} ms` : "—"}
             tone={health?.stats.latency_ms_avg == null ? "muted" : health.stats.latency_ms_avg > 500 ? "warn" : "ok"} sub="maestro → seguidora" />
        <Kpi label="Deslizamiento" icon="activity" value={health?.stats.slippage_avg != null ? `${health.stats.slippage_avg > 0 ? "+" : ""}${health.stats.slippage_avg} pts` : "—"}
             tone={health?.stats.slippage_avg == null ? "muted" : health.stats.slippage_avg > 1 ? "warn" : "ok"} sub="+ = peor que el maestro" />
      </div>

      <div className="dash-grid">
        <div className="dash-col">
          <Card title="P&L del día" icon="trendUp" right={<span className="muted small">muestras cada 15 s · realizado + flotante según NinjaTrader</span>}>
            {client ? <PnlChart accounts={accounts} client={client} master={master} compact={compact} /> : null}
          </Card>

          <Card title="Calidad de ejecución" icon="target" right={<span className="muted small">¿entran todas al precio del maestro? · últimas 40 operaciones</span>}>
            {client ? <ExecutionQuality client={client} accounts={accounts} rules={rules} master={master} compact={compact}
                                        lastFillId={audit.find((a) => a.event_type === "FOLLOWER_FILL" || a.event_type === "ENTRY_MODE_SET")?.id ?? null}
                                        onRules={(updated) => setRules(rules.map((r) => updated.find((u) => u.id === r.id) ?? r))} /> : null}
          </Card>
        </div>

        <div className="dash-col">
          <Card title={`Cuentas en juego · ${inPlay.length}`} icon="users" right={<span className="muted small">{Object.keys(prices).length ? `en vivo: ${Object.values(prices).map((p) => `${p.symbol} ${p.last}`).join(" · ")}` : "sin ticks de precio"}</span>}>
            {inPlay.length === 0 ? <Empty>{accounts.length ? "Ninguna seguidora está copiando. Actívalas en Cuentas." : "Esperando cuentas del bróker…"}</Empty> : (
              <div className="panels">
                {inPlay.map((a) => (
                  <AccountPanel key={a.account_id} a={a} role={a.account_id === master ? "master" : "follower"} link={linkOf(a.account_id)} limit={limits.get(a.account_id)}
                                lastFill={lastFill(a.account_id)} lastReject={lastReject(a.account_id)} prices={prices} busy={busy === a.account_id} compact
                                onFlatten={() => void flatten(a.account_id)} />
                ))}
              </div>
            )}
          </Card>
          <Card title="Últimos 30 días" icon="gauge" right={<span className="muted small">neto · mismas definiciones que Análisis</span>}>
            {client ? <RecentStats client={client} lastFillId={audit.find((a) => a.event_type === "FOLLOWER_FILL" || a.event_type === "ACCOUNT_FILL")?.id ?? null} onMore={() => onRoute?.("analysis")} /> : null}
          </Card>
          <Card title="Noticias de hoy" icon="news" right={<span className="muted small">EE. UU. · impacto medio y alto</span>}>
            {client ? <NewsToday client={client} /> : null}
          </Card>
        </div>
      </div>

      <div className="grid two">
        <Card title="Puente con el bróker" icon="bolt">
          {b ? (
            <div className="kv">
              <div><span>Modo</span><b>{b.mode === "mock" ? "Simulador" : `NinjaTrader${b.addon_version ? ` · addon v${b.addon_version}` : " · addon antiguo"}`}</b></div>
              <div><span>Feed maestro (5555)</span><b className={b.master_feed_up ? "ok" : "bad"}>{b.master_feed_up ? "arriba" : "caído"}</b></div>
              <div><span>Ejecutor (5556/5557)</span><b className={b.follower_feed_up ? "ok" : "bad"}>{b.follower_feed_up ? "arriba" : "caído"}</b></div>
              <div><span>Sync cuentas (5557)</span><b className={b.sync_up ? "ok" : "bad"}>{b.sync_up ? "arriba" : "caído"}</b></div>
              <div><span>Heartbeat del addon</span><b>{time(b.last_heartbeat)} <i className="muted">{ago(b.last_heartbeat)}</i></b></div>
              <div><span>Último evento IN</span><b>{time(b.last_msg_in)} <i className="muted">{ago(b.last_msg_in)}</i></b></div>
              <div><span>Última orden OUT</span><b>{time(b.last_msg_out)} <i className="muted">{ago(b.last_msg_out)}</i></b></div>
              <div><span>Errores del puente</span><b className={b.error_count ? "bad" : "ok"}>{b.error_count}</b></div>
              <div><span>Reinicios del addon (sesión)</span><b className="muted">{health?.stats.addon_restarts ?? 0}</b></div>
            </div>
          ) : <Empty>Esperando estado del engine…</Empty>}
          <p className="muted small">Latencia: del fill del maestro al fill del seguidor. Deslizamiento: precio del seguidor frente al del maestro, positivo = peor.</p>
        </Card>

        <Card title="Actividad reciente" icon="list">
          {audit.length === 0 ? <Empty>Sin actividad todavía.</Empty> : (
            <ul className="feed">
              {audit.slice(0, compact ? 12 : 8).map((a, i) => (
                <li key={a.id ?? i}><span className="muted">{time(a.timestamp)}</span><span className={`tag t-${a.event_type}`}>{a.event_type}</span><span>{a.message}</span></li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
