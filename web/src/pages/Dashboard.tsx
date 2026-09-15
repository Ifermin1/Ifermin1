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
