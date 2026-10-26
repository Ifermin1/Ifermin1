import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import type { MetricAgg, MetricsSummary } from "../lib/api";
import { Card, Kpi } from "../components/ui";
import { TimeBars } from "../components/charts";

/* Salud: cómo va el engine por dentro. Latencias (copia y órdenes), desconexiones, errores y rechazos por hora y por
 * día durante los últimos 7 días, más el estado actual. Todo sale de las métricas que el engine consolida cada hora. */
const ms = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v)} ms`);
const up = (s: number) => (s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${(s / 3600).toFixed(1)} h` : `${(s / 86400).toFixed(1)} d`);
const WD = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
const dayLabel = (d: string) => { const dt = new Date(d + "T12:00:00"); return `${WD[dt.getDay()]} ${d.slice(8)}`; };

export function Health() {
  const { client, wsRtt, health } = useStore();
  const [data, setData] = useState<MetricsSummary | null>(null);
  const [hours, setHours] = useState(168);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!client) return;
    let alive = true;
    const load = () => client.metricsSummary(hours).then((d) => { if (alive) { setData(d); setErr(null); } }).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
    load();
    const t = window.setInterval(load, 30000);
    return () => { alive = false; window.clearInterval(t); };
  }, [client, hours]);
  if (!client) return null;
  const g = data?.gauges;
  const agg = (h: Record<string, MetricAgg | string>, k: string): MetricAgg | null => (typeof h[k] === "object" ? (h[k] as MetricAgg) : null);
  const hourly = data?.hourly ?? [];
  const last24 = hourly.slice(-24);
  const lat = (k: string) => last24.map((h) => { const a = agg(h, k); return { key: h.hour, label: h.hour.slice(11, 16), value: a ? a.p95 : 0, sub: a ? `${a.n} muestras · p50 ${Math.round(a.p50)} ms · máx ${Math.round(a.max)} ms` : "sin muestras" }; });
  const daily = data?.daily ?? [];
  const cnt = (k: string) => daily.map((d) => ({ key: d.day, label: dayLabel(d.day), value: Number(d[k] ?? 0) }));
  const cur = data?.current;
  const curAgg = (k: string) => cur?.histograms[k];
  const tot = data?.totals ?? {};
  return (
    <div className="grid" data-testid="health">
      <div className="kpis">
        <Kpi label="Puente" icon="bolt" value={g ? (g.degraded ? "DEGRADADO" : g.connected ? "conectado" : "sin addon") : "…"} tone={g ? (g.degraded || !g.connected ? "bad" : "ok") : "muted"} sub={g?.addon_version ? `addon ${g.addon_version}` : ""} />
        <Kpi label="Ping al addon" icon="activity" value={ms(g?.ping_ms)} tone={g?.ping_ms !== null && g?.ping_ms !== undefined ? (g.ping_ms > 200 ? "warn" : "ok") : "muted"} sub="engine → NinjaTrader → engine" />
        <Kpi label="Consola ↔ engine" icon="share" value={ms(wsRtt)} tone={wsRtt !== null ? (wsRtt > 300 ? "warn" : "ok") : "muted"} sub="ida y vuelta del WebSocket" />
        <Kpi label="Última orden" icon="list" value={ms(g?.last_order_ms)} tone="muted" sub="confirmada por NinjaTrader" />
        <Kpi label="Engine" icon="shield" value={g ? up(g.uptime_s) : "…"} tone={g && g.loop_lag_ms > 2000 ? "warn" : "ok"} sub={g ? `sin reiniciar · retardo del ciclo ${g.loop_lag_ms} ms` : ""} />
        <Kpi label="Hoy" icon="layers" value={`${tot.fills ?? 0} fills`} tone="muted" sub={`${tot.rejected ?? 0} rechazos · ${tot.errors ?? 0} errores · ${tot.disconnects ?? 0} desconexiones desde el arranque`} />
      </div>
      {err && <div className="banner bad">{err}</div>}
      {(g?.no_ticks || health?.watchdog?.no_ticks) && <div className="banner warn">Sin datos de precio con el mercado abierto: revisa la conexión del proveedor de datos en NinjaTrader.</div>}
      <Card title="Latencia de la copia · últimas 24 h (p95 por hora)" icon="activity" right={<span className="chips"><span className="muted small">ahora: fill {curAgg("copy_fill_ms") ? `${Math.round(curAgg("copy_fill_ms")!.p95)} ms` : "—"} · envío {curAgg("copy_dispatch_ms") ? `${Math.round(curAgg("copy_dispatch_ms")!.p95)} ms` : "—"}</span>
        <div className="view-tabs">{[24, 168, 720].map((h) => <button key={h} className={hours === h ? "active" : ""} onClick={() => setHours(h)}>{h === 24 ? "24 h" : h === 168 ? "7 d" : "30 d"}</button>)}</div></span>}>
        <div className="an-two">
          <div><div className="muted small">Del fill de la maestra al fill de la seguidora (lo que de verdad tarda la copia)</div><TimeBars data={lat("copy_fill_ms")} unit="ms" warn={500} bad={1500} testId="lat-fill" label="latencia de copia" /></div>
          <div><div className="muted small">Envío al addon y confirmación (engine → NinjaTrader)</div><TimeBars data={lat("copy_dispatch_ms")} unit="ms" warn={300} bad={1000} testId="lat-dispatch" label="latencia de envío" /></div>
        </div>
      </Card>
      <div className="an-two">
        <Card title="Órdenes desde la consola · p95 por hora" icon="bolt"><TimeBars data={lat("manual_order_ms")} unit="ms" warn={500} bad={1500} label="órdenes manuales" /></Card>
        <Card title="Sincronización con NinjaTrader · p95 por hora" icon="users"><TimeBars data={lat("sync_ms")} unit="ms" warn={500} bad={2000} label="sincronización" /></Card>
      </div>
      <Card title={`Incidencias por día · ${hours === 24 ? "hoy" : hours === 168 ? "7 días" : "30 días"}`} icon="shield">
        <div className="an-two">
          <div><div className="muted small">Desconexiones del addon (sin heartbeat, caído, reconectado)</div><TimeBars data={cnt("disconnects")} warn={1} bad={5} label="desconexiones" testId="inc-disc" /></div>
          <div><div className="muted small">Errores, rechazos y copias bloqueadas</div><TimeBars data={daily.map((d) => ({ key: d.day, label: dayLabel(d.day), value: Number(d.errors ?? 0) + Number(d.rejected ?? 0) + Number(d.blocked ?? 0), sub: `${d.errors ?? 0} errores · ${d.rejected ?? 0} rechazos · ${d.blocked ?? 0} bloqueadas` }))} warn={1} bad={10} label="incidencias" /></div>
        </div>
        <div className="an-two" style={{ marginTop: 12 }}>
          <div><div className="muted small">Fills copiados por día</div><TimeBars data={cnt("fills")} label="fills" /></div>
          <div><div className="muted small">Modo degradado y mercado sin datos (episodios por día)</div><TimeBars data={daily.map((d) => ({ key: d.day, label: dayLabel(d.day), value: Number(d.degraded ?? 0) + Number(d.no_ticks ?? 0), sub: `${d.degraded ?? 0} degradado · ${d.no_ticks ?? 0} sin datos` }))} warn={1} bad={3} label="episodios" /></div>
        </div>
      </Card>
      <Card title="Últimas 24 horas, hora a hora" icon="list">
        {last24.length === 0 ? <p className="muted small">Sin datos todavía: las métricas se consolidan cada hora.</p> : (
          <div className="table-wrap"><table className="an-table" data-testid="health-table"><thead><tr><th>Hora</th><th>Fills</th><th>Rechazos</th><th>Errores</th><th>Descon.</th><th>Copia p50/p95</th><th>Envío p95</th><th>Órdenes p95</th><th>Ping p95</th></tr></thead><tbody>
            {[...last24].reverse().map((h) => { const f = agg(h, "copy_fill_ms"), d = agg(h, "copy_dispatch_ms"), m = agg(h, "manual_order_ms"), p = agg(h, "ping_ms"); const n = (k: string) => (agg(h, k)?.n ?? 0); return (
              <tr key={h.hour}><td>{h.hour.slice(5, 16).replace("T", " ")}</td><td>{n("fills") || ""}</td><td className={n("rejected") ? "bad" : ""}>{n("rejected") || ""}</td><td className={n("errors") ? "bad" : ""}>{n("errors") || ""}</td><td className={n("disconnects") ? "warn" : ""}>{n("disconnects") || ""}</td>
                <td>{f ? `${Math.round(f.p50)} / ${Math.round(f.p95)} ms` : ""}</td><td>{d ? `${Math.round(d.p95)} ms` : ""}</td><td>{m ? `${Math.round(m.p95)} ms` : ""}</td><td>{p ? `${Math.round(p.p95)} ms` : ""}</td></tr>); })}
          </tbody></table></div>
        )}
        <p className="muted tiny">Para Prometheus/Grafana: <code>GET /api/metrics</code> con el token.</p>
      </Card>
    </div>
  );
}
