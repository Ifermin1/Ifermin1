import { useStore } from "../lib/store";
import { ago, money, time } from "../lib/format";
import { Card, Empty, Stat } from "../components/ui";

export function Dashboard() {
  const { health, accounts, rules, audit, risk } = useStore();
  const b = health?.bridge;
  const total = accounts.reduce((s, a) => s + a.balance, 0);
  const active = rules.filter((r) => r.enabled).length;
  return (
    <div className="grid">
      <div className="stats">
        <Stat label="Capital total" value={money(total)} />
        <Stat label="Cuentas" value={accounts.length} />
        <Stat label="Reglas activas" value={`${active} / ${rules.length}`} tone={active ? "ok" : "muted"} />
        <Stat label="Órdenes replicadas" value={health?.stats.orders_out ?? 0} />
        <Stat label="Fills seguidores" value={health?.stats.fills ?? 0} tone={health?.stats.fills ? "ok" : "muted"} />
        <Stat label="Rechazadas" value={(health?.stats.rejected ?? 0) + (health?.stats.blocked ?? 0)} tone={(health?.stats.rejected || health?.stats.blocked) ? "bad" : "muted"} />
        <Stat label="Errores" value={health?.stats.errors ?? 0} tone={health?.stats.errors ? "bad" : "ok"} />
        <Stat label="Latencia copia" value={health?.stats.latency_ms_avg != null ? `${health.stats.latency_ms_avg} ms` : "—"}
              tone={health?.stats.latency_ms_avg == null ? "muted" : health.stats.latency_ms_avg > 500 ? "warn" : "ok"} />
        <Stat label="Deslizamiento medio" value={health?.stats.slippage_avg != null ? `${health.stats.slippage_avg > 0 ? "+" : ""}${health.stats.slippage_avg} pts` : "—"}
              tone={health?.stats.slippage_avg == null ? "muted" : health.stats.slippage_avg > 1 ? "warn" : "ok"} />
      </div>
      <p className="muted small">Latencia: desde que el engine recibe el fill del maestro hasta que recibe el fill del seguidor. Deslizamiento: precio del seguidor frente al del maestro, positivo = peor para el seguidor.</p>

      {health?.addon_outdated && (
        <Card className="alert-card"><strong>Addon de NinjaTrader desactualizado</strong> ({health.bridge.addon_version ? `v${health.bridge.addon_version}` : "sin versión"}; se requiere v{health.min_addon_version}).
          Las protecciones (cierre de emergencia, posiciones, detección de pérdidas) no funcionan hasta recompilarlo.</Card>
      )}
      {health?.stats.seq_gaps ? (
        <Card className="alert-card"><strong>Mensajes perdidos del addon:</strong> {health.stats.seq_gaps} en esta sesión. Revisa la sincronización de las seguidoras en <i>Cuentas</i>.</Card>
      ) : null}
      {accounts.some((a) => a.desync) && (
        <Card className="alert-card"><strong>Seguidoras desincronizadas:</strong> {accounts.filter((a) => a.desync).map((a) => a.account_id).join(", ")}. Ve a <i>Cuentas</i> para igualarlas o cerrarlas.</Card>
      )}
      {risk?.addon_silent && (
        <Card className="alert-card"><strong>Sin heartbeat del addon</strong> desde hace más de 30 s: NinjaTrader no está enviando operaciones. Revisa que esté abierto y el addon cargado.</Card>
      )}
      {risk?.session_closed && (
        <Card className="alert-card"><strong>Sesión cerrada por horario</strong> ({risk.schedule.flatten_at}). No se copia nada hasta mañana; en <i>Riesgo</i> puedes reabrir.</Card>
      )}
      {risk?.limits.some((l) => l.trading_halted && l.halted_reason === "daily_loss") && (
        <Card className="alert-card"><strong>Límite de pérdida diaria alcanzado:</strong> {risk.limits.filter((l) => l.halted_reason === "daily_loss").map((l) => l.account_id).join(", ")}. Cuenta pausada y cerrada; se reanuda a mano en <i>Riesgo</i>.</Card>
      )}
      {risk?.kill_switch && (
        <Card className="alert-card">
          <strong>Kill switch activo.</strong> No se está replicando ninguna orden. {risk.kill_switch_reason && <>Motivo: {risk.kill_switch_reason}.</>}
        </Card>
      )}

      <Card title="Puente con el bróker">
        {b ? (
          <div className="kv">
            <div><span>Modo</span><b>{b.mode === "mock" ? "Simulador" : `NinjaTrader (ZMQ)${b.addon_version ? ` · addon v${b.addon_version}` : " · addon antiguo"}`}</b></div>
            <div><span>Feed maestro (5555)</span><b className={b.master_feed_up ? "ok" : "bad"}>{b.master_feed_up ? "arriba" : "caído"}</b></div>
            <div><span>Ejecutor (5556)</span><b className={b.follower_feed_up ? "ok" : "bad"}>{b.follower_feed_up ? "arriba" : "caído"}</b></div>
            <div><span>Sync cuentas (5557)</span><b className={b.sync_up ? "ok" : "bad"}>{b.sync_up ? "arriba" : "caído"}</b></div>
            <div><span>Heartbeat del addon</span><b>{time(b.last_heartbeat)} <i className="muted">{ago(b.last_heartbeat)}</i></b></div>
            <div><span>Último evento IN</span><b>{time(b.last_msg_in)} <i className="muted">{ago(b.last_msg_in)}</i></b></div>
            <div><span>Última orden OUT</span><b>{time(b.last_msg_out)} <i className="muted">{ago(b.last_msg_out)}</i></b></div>
            <div><span>Errores del puente</span><b className={b.error_count ? "bad" : "ok"}>{b.error_count}</b></div>
            <div><span>Reinicios del addon (sesión)</span><b className="muted">{health?.stats.addon_restarts ?? 0}</b></div>
          </div>
        ) : <Empty>Esperando estado del engine…</Empty>}
      </Card>

      <Card title="Actividad reciente">
        {audit.length === 0 ? <Empty>Sin actividad todavía.</Empty> : (
          <ul className="feed">
            {audit.slice(0, 8).map((a, i) => (
              <li key={a.id ?? i}><span className="muted">{time(a.timestamp)}</span><span className={`tag t-${a.event_type}`}>{a.event_type}</span><span>{a.message}</span></li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
