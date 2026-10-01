import { useEffect, useMemo, useState } from "react";
import type { AnalysisData, Client } from "../lib/api";
import { byHour, dayStats, tradeStats, netOf } from "../lib/stats";
import { EquityCurve, SignedBars } from "../components/charts";
import { moneyShort } from "../lib/format";
import { useThrottled } from "../lib/hooks";

/* Tarjeta de Inicio: los últimos 30 días en cifras (neto, aciertos, factor de beneficio, esperanza, drawdown máximo,
 * mejor y peor jornada), la curva de capital y el neto por hora de entrada. Mismas definiciones que Análisis → Estadísticas. */
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export function RecentStats({ client, account = "", lastFillId, onMore }: { client: Client; account?: string; lastFillId: number | null; onMore: () => void }) {
  const [data, setData] = useState<AnalysisData | null>(null);
  const trigger = useThrottled(lastFillId, 5000);     // como mucho una recarga cada 5 s aunque lleguen fills seguidos
  useEffect(() => {
    let alive = true;
    const from = new Date(); from.setDate(from.getDate() - 30);
    const to = new Date(); to.setDate(to.getDate() + 1);
    client.analysis(iso(from), iso(to), account).then((d) => { if (alive) setData(d); }).catch(() => {});
    return () => { alive = false; };
  }, [client, account, trigger]);
  const trades = data?.trades ?? [];
  const ts = useMemo(() => tradeStats(trades, netOf), [trades]);
  const ds = useMemo(() => dayStats((data?.days ?? []).filter((d) => d.trades || d.pnl_broker !== null).map((d) => ({ day: d.day, net: Math.round(((d.trades ? d.pnl_trades : (d.pnl_broker ?? 0)) - d.commissions) * 100) / 100 }))), [data]);
  if (!data) return <p className="empty">Cargando…</p>;
  if (!trades.length && !ds.n) return <p className="empty">Todavía no hay operaciones cerradas en los últimos 30 días.</p>;
  const tone = (x: number) => (x > 0 ? "ok" : x < 0 ? "bad" : "");
  return (
    <div className="recent" data-testid="recent-stats">
      <div className="recent-grid">
        <div><span className="stat-label">Neto 30 d</span><b className={tone(ds.net)}>{moneyShort(ds.net)}</b><span className="muted small">{ts.n} op. · {ds.green}/{ds.red} días</span></div>
        <div><span className="stat-label">Aciertos</span><b>{ts.winRate === null ? "—" : `${Math.round(ts.winRate * 100)} %`}</b><span className="muted small">{ts.wins} / {ts.losses}</span></div>
        <div><span className="stat-label">Factor benef.</span><b className={ts.pf === null ? "" : ts.pf >= 1 ? "ok" : "bad"}>{ts.pf === null ? "—" : ts.pf === Infinity ? "∞" : ts.pf.toFixed(2)}</b><span className="muted small">ganado ÷ perdido</span></div>
        <div><span className="stat-label">Esperanza</span><b className={ts.expectancy === null ? "" : tone(ts.expectancy)}>{ts.expectancy === null ? "—" : moneyShort(ts.expectancy)}</b><span className="muted small">por operación</span></div>
        <div><span className="stat-label">Drawdown máx.</span><b className={ds.maxDd ? "bad" : ""}>{ds.maxDd ? moneyShort(-ds.maxDd) : "—"}</b><span className="muted small">de la curva</span></div>
        <div><span className="stat-label">Mejor / peor día</span><b><span className="ok">{ds.best ? moneyShort(ds.best.net) : "—"}</span> <span className="muted">/</span> <span className="bad">{ds.worst && ds.worst.net < 0 ? moneyShort(ds.worst.net) : "—"}</span></b><span className="muted small">racha {ds.streak > 0 ? `+${ds.streak}` : ds.streak} días</span></div>
      </div>
      <EquityCurve points={ds.equity} height={150} compact />
      <div className="recent-hours"><span className="stat-label">Neto por hora de entrada</span><SignedBars data={byHour(trades)} height={120} showN /></div>
      <button className="link small" onClick={onMore}>Ver todas las estadísticas en Análisis →</button>
    </div>
  );
}
