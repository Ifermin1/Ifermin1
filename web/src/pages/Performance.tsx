import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../lib/store";
import type { DayStat, MonthStats, Trade } from "../lib/api";
import { money, moneyShort, signedMoney } from "../lib/format";
import { Card, Empty } from "../components/ui";

/* Calendario de rendimiento: un mes de un vistazo (P&L neto por día, operaciones, aciertos), indicadores del mes,
 * curva acumulada y el detalle de un día con sus operaciones. Verde/rojo con tres intensidades según el tamaño del día. */
const DOW = ["L", "M", "X", "J", "V", "S", "D"];
const monthKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const short = (n: number) => { const a = Math.abs(n); const s = a >= 1000 ? `${(a / 1000).toFixed(a >= 10000 ? 0 : 1)}k` : a.toFixed(0); return (n < 0 ? "−" : n > 0 ? "+" : "") + s; };
const hm = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", hour12: false }) : "—");
const dur = (a: string | null, b: string) => { if (!a) return "—"; const s = Math.max(0, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000)); return s < 60 ? `${s} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`; };
const fmtDay = (day: string) => new Date(day + "T12:00:00").toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long" });
const M = { l: 52, r: 12, t: 12, b: 24 };

export function Performance() {
  const { client, accounts, health, audit, density } = useStore();
  const compact = density === "compact";
  const [cursor, setCursor] = useState(() => { const d = new Date(); d.setDate(1); d.setHours(0, 0, 0, 0); return d; });
  const [account, setAccount] = useState("");
  const [data, setData] = useState<MonthStats | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [width, setWidth] = useState(600);
  const box = useRef<HTMLDivElement>(null);
  const key = monthKey(cursor);
  const lastTradeId = audit.find((a) => a.event_type === "FOLLOWER_FILL" || a.event_type === "MASTER_RECEIVED" || a.event_type === "ACCOUNT_FILL")?.id ?? null;

  useEffect(() => {
    if (!client) return;
    let alive = true;
    const t = window.setTimeout(() => client.month(key, account).then((m) => { if (alive) setData(m); }).catch(() => {}), lastTradeId === null ? 0 : 600);
    return () => { alive = false; window.clearTimeout(t); };
  }, [client, key, account, lastTradeId]);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver((e) => setWidth(Math.max(240, Math.floor(e[0].contentRect.width))));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  useEffect(() => { setSelected(null); }, [key, account]);

  const label = (id: string) => accounts.find((a) => a.account_id.toLowerCase() === id.toLowerCase())?.alias || id;
  const days = useMemo(() => new Map((data?.days ?? []).map((d) => [d.day, d])), [data]);
  const trades = data?.trades ?? [];
  const today = new Date().toISOString().slice(0, 10);

  // ---- indicadores del mes ----
  const kpi = useMemo(() => {
    const ds = data?.days ?? [];
    const net = ds.reduce((s, d) => s + d.net, 0);
    const green = ds.filter((d) => d.net > 0).length, red = ds.filter((d) => d.net < 0).length;
    const wins = trades.filter((t) => t.pnl > 0), losses = trades.filter((t) => t.pnl < 0);
    const gw = wins.reduce((s, t) => s + t.pnl, 0), gl = -losses.reduce((s, t) => s + t.pnl, 0);
    const best = ds.reduce<DayStat | null>((b, d) => (b === null || d.net > b.net ? d : b), null);
    const worst = ds.reduce<DayStat | null>((b, d) => (b === null || d.net < b.net ? d : b), null);
    let streak = 0;
    for (const d of [...ds].reverse()) { if (d.net === 0) continue; const sgn = d.net > 0 ? 1 : -1; if (streak === 0) streak = sgn; else if (Math.sign(streak) === sgn) streak += sgn; else break; }
    const fees = ds.reduce((s, d) => s + d.commissions, 0);
    return { net, fees, green, red, trades: trades.length, winRate: trades.length ? wins.length / trades.length : null,
             pf: gl > 0 ? gw / gl : gw > 0 ? Infinity : null, avgWin: wins.length ? gw / wins.length : null, avgLoss: losses.length ? gl / losses.length : null,
             best, worst, streak };
  }, [data, trades]);

  // ---- rejilla del calendario (semanas de lunes a domingo) ----
  const cells = useMemo(() => {
    const y = cursor.getFullYear(), m = cursor.getMonth();
    const first = new Date(y, m, 1), n = new Date(y, m + 1, 0).getDate();
    const pad = (first.getDay() + 6) % 7;
    const out: { day: string | null; num: number }[] = [];
    for (let i = 0; i < pad; i++) out.push({ day: null, num: 0 });
    for (let d = 1; d <= n; d++) out.push({ day: `${key}-${String(d).padStart(2, "0")}`, num: d });
    while (out.length % 7) out.push({ day: null, num: 0 });
    const weeks: { day: string | null; num: number }[][] = [];
    for (let i = 0; i < out.length; i += 7) weeks.push(out.slice(i, i + 7));
    return weeks;
  }, [cursor, key]);
  const maxAbs = Math.max(1, ...(data?.days ?? []).map((d) => Math.abs(d.net)));
  const tone = (d: DayStat | undefined) => {
    if (!d || d.net === 0) return "";
    const lvl = Math.abs(d.net) / maxAbs > 0.66 ? 3 : Math.abs(d.net) / maxAbs > 0.33 ? 2 : 1;
    return (d.net > 0 ? "g" : "r") + lvl;
  };

  // ---- gráfico: barras diarias + acumulado ----
  const series = useMemo(() => { let acc = 0; return (data?.days ?? []).map((d) => { acc += d.net; return { day: d.day, net: d.net, cum: acc }; }); }, [data]);
  const W = width, H = compact ? 160 : 200, iw = W - M.l - M.r, ih = H - M.t - M.b;
  let lo = 0, hi = 0;
  for (const s of series) { lo = Math.min(lo, s.net, s.cum); hi = Math.max(hi, s.net, s.cum); }
  if (hi - lo < 1) { lo -= 100; hi += 100; }
  const padY = (hi - lo) * 0.08; lo -= padY; hi += padY;
  const x = (i: number) => M.l + ((i + 0.5) / Math.max(1, series.length)) * iw;
  const y = (v: number) => M.t + (1 - (v - lo) / (hi - lo)) * ih;
  const bw = Math.max(2, Math.min(18, (iw / Math.max(1, series.length)) * 0.55));
  const yTicks = useMemo(() => { const span = hi - lo; const raw = span / 4; const p = 10 ** Math.floor(Math.log10(raw)); const mlt = raw / p; const st = (mlt < 1.5 ? 1 : mlt < 3.5 ? 2.5 : mlt < 7.5 ? 5 : 10) * p; const t: number[] = []; for (let v = Math.ceil(lo / st) * st; v <= hi; v += st) t.push(v); return t; }, [lo, hi]);
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < M.l || px > W - M.r || !series.length) { setHover(null); return; }
    setHover(Math.min(series.length - 1, Math.max(0, Math.floor(((px - M.l) / iw) * series.length))));
  };
  const hov = hover !== null ? series[hover] : null;

  const dayTrades: Trade[] = selected ? trades.filter((t) => t.day === selected) : [];
  const sel = selected ? days.get(selected) : undefined;
  const inPlay = accounts.filter((a) => a.enabled);
  const accountOptions = Array.from(new Set([...inPlay.map((a) => a.account_id), ...(data?.accounts ?? [])]));
  const master = health?.bridge.master_account ?? null;
  if (!client) return null;

  return (
    <div className="grid" data-testid="performance">
      <Card>
        <div className="perf-bar">
          <div className="month-nav">
            <button className="chip-btn" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))} aria-label="Mes anterior">‹</button>
            <b>{cursor.toLocaleDateString("es-ES", { month: "long", year: "numeric" })}</b>
            <button className="chip-btn" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))} aria-label="Mes siguiente">›</button>
            <button className="chip-btn" onClick={() => { const d = new Date(); d.setDate(1); d.setHours(0, 0, 0, 0); setCursor(d); }}>Hoy</button>
          </div>
          <label className="inline">Cuenta
            <select value={account} onChange={(e) => setAccount(e.target.value)} data-testid="perf-account">
              <option value="">Todas</option>
              {accountOptions.map((id) => <option key={id} value={id}>{label(id)}{id === master ? " · maestra" : ""}</option>)}
            </select>
          </label>
        </div>
      </Card>

      <div className="kpis" data-testid="perf-kpis">
        <div className="kpi"><span>P&L del mes</span><b className={kpi.net > 0 ? "ok" : kpi.net < 0 ? "bad" : "muted"} title={signedMoney(kpi.net)}>{moneyShort(kpi.net)}</b><i>{kpi.fees ? `neto · comisiones ${moneyShort(kpi.fees).replace("+", "")}` : "sin comisiones anotadas"}</i></div>
        <div className="kpi"><span>Días</span><b><span className="ok">{kpi.green}</span> <span className="muted">/</span> <span className="bad">{kpi.red}</span></b><i>verdes / rojos</i></div>
        <div className="kpi"><span>Operaciones</span><b>{kpi.trades}</b><i>{kpi.winRate === null ? "—" : `${Math.round(kpi.winRate * 100)} % ganadoras`}</i></div>
        <div className="kpi"><span>Factor de beneficio</span><b className={kpi.pf === null ? "muted" : kpi.pf >= 1.5 ? "ok" : kpi.pf >= 1 ? "warn" : "bad"}>{kpi.pf === null ? "—" : kpi.pf === Infinity ? "∞" : kpi.pf.toFixed(2)}</b><i>ganado ÷ perdido</i></div>
        <div className="kpi"><span>Media ganadora</span><b className="ok">{kpi.avgWin === null ? "—" : moneyShort(kpi.avgWin)}</b><i>por operación</i></div>
        <div className="kpi"><span>Media perdedora</span><b className="bad">{kpi.avgLoss === null ? "—" : moneyShort(-kpi.avgLoss)}</b><i>por operación</i></div>
        <div className="kpi"><span>Mejor día</span><b className="ok">{kpi.best ? moneyShort(kpi.best.net) : "—"}</b><i>{kpi.best ? fmtDay(kpi.best.day) : ""}</i></div>
        <div className="kpi"><span>Peor día</span><b className="bad">{kpi.worst && kpi.worst.net < 0 ? moneyShort(kpi.worst.net) : "—"}</b><i>{kpi.worst && kpi.worst.net < 0 ? fmtDay(kpi.worst.day) : kpi.streak ? `racha: ${Math.abs(kpi.streak)} ${kpi.streak > 0 ? "verdes" : "rojos"}` : ""}</i></div>
      </div>

      <Card title="Calendario" right={<span className="muted small">{account ? label(account) : `todas · ${(data?.accounts ?? []).length} cuentas`} · P&L del bróker por día; toca un día para ver sus operaciones</span>}>
        {data && data.days.length === 0 && <Empty>Sin datos en {cursor.toLocaleDateString("es-ES", { month: "long", year: "numeric" })}. El calendario se rellena con cada día operado.</Empty>}
        <div className="cal with-weeks" data-testid="calendar">
          {DOW.map((d) => <div key={d} className="dow">{d}</div>)}
          <div className="dow week-total">Semana</div>
          {cells.map((week, wi) => {
            const wk = week.reduce((s, c) => { const d = c.day ? days.get(c.day) : undefined; return d ? { net: s.net + d.net, n: s.n + d.trades } : s; }, { net: 0, n: 0 });
            return [
              ...week.map((c, i) => {
                if (!c.day) return <div key={`p${wi}-${i}`} className="day-cell pad" />;
                const d = days.get(c.day);
                return (
                  <button key={c.day} className={`day-cell ${tone(d)} ${c.day === today ? "today" : ""} ${selected === c.day ? "selected" : ""} ${d ? "" : "empty"}`}
                          onClick={() => d && setSelected(selected === c.day ? null : c.day)} data-day={c.day} title={d ? `${fmtDay(c.day)}: ${signedMoney(d.net)}, ${d.trades} operaciones` : undefined}>
                    <span className="dnum">{c.num}</span>
                    {d && <><span className="dpnl">{short(d.net)}</span>
                      <span className="dmeta">{d.trades ? <>{d.trades} op<span className="dwin"> · {Math.round((100 * d.wins) / d.trades)} %</span></> : "0 op"}</span></>}
                  </button>
                );
              }),
              <div key={`w${wi}`} className={`day-cell week-total`}><span className="dnum">sem.</span>
                <span className={`dpnl ${wk.net > 0 ? "ok" : wk.net < 0 ? "bad" : "muted"}`}>{wk.n || wk.net ? short(wk.net) : "—"}</span>
                <span className="dmeta">{wk.n ? `${wk.n} op` : ""}</span></div>,
            ];
          })}
        </div>
        <div className="cal-legend"><span><i style={{ background: "rgba(52,211,153,.34)" }} />día ganador (más intenso = mayor)</span><span><i style={{ background: "rgba(248,113,113,.34)" }} />día perdedor</span><span><i style={{ borderStyle: "dashed", borderColor: "var(--accent)" }} />hoy</span></div>
      </Card>

      {selected && sel && (
        <Card title={`Detalle · ${fmtDay(selected)}`} right={<button className="chip-btn" onClick={() => setSelected(null)}>Cerrar</button>}>
          <div className="day-detail" data-testid="day-detail">
            <div className="day-head">
              <b className={`num ${sel.net > 0 ? "ok" : sel.net < 0 ? "bad" : ""}`} style={{ fontSize: "var(--big)" }}>{signedMoney(sel.net)}</b>
              <span className="muted small">{sel.pnl_broker !== null ? `bróker ${signedMoney(sel.pnl_broker)}` : "suma de operaciones"}{sel.commissions ? ` · comisiones ${money(sel.commissions)} (${sel.contracts} contr.)` : ""} · {sel.trades} operaciones · {sel.wins} ganadoras · {sel.losses} perdedoras{sel.trades ? ` · mejor ${signedMoney(sel.best)} · peor ${signedMoney(sel.worst)}` : ""}</span>
            </div>
            {!account && Object.keys(sel.accounts).length > 1 && (
              <div className="by-account">
                {Object.entries(sel.accounts).sort(([a], [b]) => a.localeCompare(b)).map(([id, s]) => {
                  const v = s.pnl_broker ?? s.pnl_trades;
                  return <span key={id} className="chip">{label(id)}<b className={v > 0 ? "ok" : v < 0 ? "bad" : "muted"}>{signedMoney(v)}</b><span className="muted"> · {s.trades} op</span></span>;
                })}
              </div>
            )}
            {dayTrades.length === 0 ? <Empty>Sin operaciones cerradas registradas ese día (el P&L viene del bróker).</Empty> : (
              <div className="table-wrap"><table className="trades">
                <thead><tr><th>Entrada</th><th>Salida</th>{!account && <th>Cuenta</th>}<th>Símbolo</th><th>Lado</th><th className="num">Contr.</th><th className="num">Precio ent.</th><th className="num">Precio sal.</th><th className="num">P&L</th><th className="num">Comis.</th><th className="num">Duración</th></tr></thead>
                <tbody>{dayTrades.map((t) => (
                  <tr key={t.id}>
                    <td className="muted">{hm(t.opened_at)}</td><td className="muted">{hm(t.closed_at)}</td>
                    {!account && <td>{label(t.account_id)}</td>}
                    <td>{t.symbol}</td><td className={`side-${t.side}`}>{t.side === "long" ? "Largo" : "Corto"}</td>
                    <td className="num">{t.quantity}</td><td className="num">{t.entry_price}</td><td className="num">{t.exit_price}</td>
                    <td className={`num ${t.pnl > 0 ? "ok" : t.pnl < 0 ? "bad" : ""}`}>{signedMoney(t.pnl)}</td>
                    <td className="num muted">{t.commissions ? money(t.commissions) : "—"}</td><td className="num muted">{dur(t.opened_at, t.closed_at)}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </div>
        </Card>
      )}

      <Card title="Evolución del mes" right={<span className="muted small">barras: P&L neto de cada día · línea: acumulado</span>}>
        <div className="chart-wrap perf-chart" ref={box}>
          {series.length === 0 ? <Empty>Sin días operados en este mes.</Empty> : (
            <>
              <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="P&L diario y acumulado del mes" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
                {yTicks.map((v) => (
                  <g key={v}>
                    <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className={Math.abs(v) < 1e-9 ? "zero" : "grid"} />
                    <text x={M.l - 8} y={y(v) + 4} textAnchor="end" className="tick">{Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(v % 1000 ? 1 : 0)}k` : Math.round(v)}</text>
                  </g>
                ))}
                {series.map((s, i) => { const st = Math.max(1, Math.ceil(series.length / 8)); return (i % st === 0 || (i === series.length - 1 && (i % st) * 2 >= st)) && (
                  <text key={s.day} x={x(i)} y={H - 6} textAnchor="middle" className="tick">{Number(s.day.slice(8))}</text>
                ); })}
                {series.map((s, i) => <rect key={s.day} x={x(i) - bw / 2} y={Math.min(y(0), y(s.net))} width={bw} height={Math.max(1, Math.abs(y(s.net) - y(0)))} rx={2} className={`bar ${s.net < 0 ? "neg" : ""}`} onClick={() => setSelected(s.day)} />)}
                <path d={series.map((s, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(s.cum).toFixed(1)}`).join(" ")} className="cum" />
                {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={M.t} y2={H - M.b} className="crosshair" />}
                {hov && <circle cx={x(hover!)} cy={y(hov.cum)} r={4} className="cum-dot" />}
              </svg>
              {hov && (
                <div className="tooltip" style={{ [x(hover!) > W * 0.6 ? "right" : "left"]: x(hover!) > W * 0.6 ? W - x(hover!) + 10 : x(hover!) + 10 }}>
                  <div className="muted small">{fmtDay(hov.day)}</div>
                  <div>día <b className={`num ${hov.net > 0 ? "ok" : hov.net < 0 ? "bad" : ""}`}>{signedMoney(hov.net)}</b></div>
                  <div>acumulado <b className={`num ${hov.cum > 0 ? "ok" : hov.cum < 0 ? "bad" : ""}`}>{signedMoney(hov.cum)}</b></div>
                </div>
              )}
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
