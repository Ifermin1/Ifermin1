import { useState } from "react";
import { useStore } from "../lib/store";
import { ago, time } from "../lib/format";
import { Card, Empty, Kpi } from "../components/ui";
import { AccountPanel } from "../components/AccountPanel";
import { PnlChart } from "../components/PnlChart";
import { ExecutionQuality } from "../components/ExecutionQuality";

/** Inicio = sala de control: alertas, P&L del día, cuentas en juego y estado del puente. */
export function Dashboard() {
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

  return (
    <div className="grid">
      {health?.addon_outdated && (
        <Card className="alert-card"><strong>Addon de NinjaTrader desactualizado</strong> ({health.bridge.addon_version ? `v${health.bridge.addon_version}` : "sin versión"}; se requiere v{health.min_addon_version}).
          Las protecciones (cierre de emergencia, posiciones, órdenes vivas) no funcionan hasta recompilarlo.</Card>
      )}
      {health?.stats.seq_gaps ? (
        <Card className="alert-card"><strong>Mensajes perdidos del addon:</strong> {health.stats.seq_gaps} en esta sesión. Revisa la sincronización de las seguidoras en <i>Cuentas</i>.</Card>
      ) : null}
      {accounts.some((a) => a.desync) && (
        <Card className="alert-card"><strong>Seguidoras desincronizadas:</strong> {accounts.filter((a) => a.desync).map((a) => a.account_id).join(", ")}. Ve a <i>Cuentas</i> para igualarlas o cerrarlas.</Card>
      )}
      {risk?.addon_silent && (
        <Card className="alert-card"><strong>Sin heartbeat del addon</strong> desde hace más de 15 s: NinjaTrader no está enviando operaciones. Revisa que esté abierto y el addon cargado.</Card>
      )}
      {risk?.session_closed && (
        <Card className="alert-card"><strong>Sesión cerrada por horario</strong> ({risk.schedule.flatten_at}). No se copia nada hasta mañana; en <i>Riesgo</i> puedes reabrir.</Card>
      )}
      {risk?.limits.some((l) => l.trading_halted && l.halted_reason === "daily_loss") && (
        <Card className="alert-card"><strong>Límite de pérdida diaria alcanzado:</strong> {risk.limits.filter((l) => l.halted_reason === "daily_loss").map((l) => l.account_id).join(", ")}. Cuenta pausada y cerrada; se reanuda a mano en <i>Riesgo</i>.</Card>
      )}
      {risk?.limits.some((l) => l.trading_halted && l.halted_reason === "daily_profit") && (
        <Card className="alert-card"><strong>Objetivo de ganancia diaria alcanzado:</strong> {risk.limits.filter((l) => l.halted_reason === "daily_profit").map((l) => l.account_id).join(", ")}. Cuenta pausada y cerrada para asegurar la ganancia; se reanuda a mano en <i>Riesgo</i>.</Card>
      )}
      {risk?.limits.some((l) => l.trading_halted && l.halted_reason === "drawdown") && (
        <Card className="alert-card"><strong>Límite de drawdown alcanzado:</strong> {risk.limits.filter((l) => l.halted_reason === "drawdown").map((l) => l.account_id).join(", ")}. Cuenta pausada y cerrada antes de tocar el suelo del prop firm; se reanuda a mano en <i>Riesgo</i> (solo si vuelve a tener margen).</Card>
      )}
      {accounts.some((a) => a.enabled && a.drawdown.pct !== null && a.drawdown.pct >= 80 && !limits.get(a.account_id)?.trading_halted) && (
        <Card className="alert-card"><strong>Drawdown al límite:</strong> {accounts.filter((a) => a.enabled && a.drawdown.pct !== null && a.drawdown.pct >= 80 && !limits.get(a.account_id)?.trading_halted).map((a) => `${a.alias || a.account_id} (${a.drawdown.pct!.toFixed(0)} %, quedan ${Math.round(a.drawdown.room ?? 0)} $)`).join(", ")}. Cerca del suelo del prop firm: reduce o cierra antes de que el engine lo haga por ti.</Card>
      )}
      {risk?.kill_switch && (
        <Card className="alert-card">
          <strong>Kill switch activo.</strong> No se está replicando ninguna orden. {risk.kill_switch_reason && <>Motivo: {risk.kill_switch_reason}.</>}
        </Card>
      )}

      <div className="stats">
        <Kpi label="Copiando" icon="users" value={`${copying.length} / ${accounts.filter((a) => a.enabled && a.account_id !== master).length}`} tone={copying.length ? "ok" : "muted"}
             ring={accounts.filter((a) => a.enabled && a.account_id !== master).length ? (100 * copying.length) / accounts.filter((a) => a.enabled && a.account_id !== master).length : null} sub="seguidoras activas" />
        <Kpi label="Posiciones" icon="layers" value={openPositions} tone={openPositions ? "warn" : "muted"} sub={openPositions ? "abiertas ahora" : "todo plano"} />
        <Kpi label="Órdenes replicadas" icon="copy" value={health?.stats.orders_out ?? 0} tone="accent" sub="en esta sesión" />
        <Kpi label="Fills seguidoras" icon="check" value={health?.stats.fills ?? 0} tone={health?.stats.fills ? "ok" : "muted"} sub="ejecuciones confirmadas" />
        <Kpi label="Rechazadas · bloqueadas" icon="x" value={`${health?.stats.rejected ?? 0} · ${health?.stats.blocked ?? 0}`} tone={(health?.stats.rejected || health?.stats.blocked) ? "bad" : "muted"} sub="por el bróker · por riesgo" />
        <Kpi label="Errores" icon="bolt" value={health?.stats.errors ?? 0} tone={health?.stats.errors ? "bad" : "ok"} sub="del puente" />
        <Kpi label="Latencia copia" icon="clock" value={health?.stats.latency_ms_avg != null ? `${health.stats.latency_ms_avg} ms` : "—"}
             tone={health?.stats.latency_ms_avg == null ? "muted" : health.stats.latency_ms_avg > 500 ? "warn" : "ok"} sub="maestro → seguidora, media" />
        <Kpi label="Deslizamiento" icon="activity" value={health?.stats.slippage_avg != null ? `${health.stats.slippage_avg > 0 ? "+" : ""}${health.stats.slippage_avg} pts` : "—"}
             tone={health?.stats.slippage_avg == null ? "muted" : health.stats.slippage_avg > 1 ? "warn" : "ok"} sub="positivo = peor que el maestro" />
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
