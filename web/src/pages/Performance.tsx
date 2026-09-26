import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../lib/store";
import type { DayStat, MonthStats, Trade } from "../lib/api";
import { money, moneyShort, signedMoney } from "../lib/format";
import { Card, Empty, Kpi } from "../components/ui";
import { Icon } from "../components/Icons";
import { ShareModal, type ShareData } from "../components/ShareCard";
import { NewsPanel } from "../components/NewsPanel";

/* Calendario de rendimiento: un mes de un vistazo (P&L neto por día, operaciones, aciertos), indicadores del mes,
 * curva acumulada y el detalle de un día con sus operaciones. Verde/rojo con tres intensidades según el tamaño del día. */
const DOW = ["L", "M", "X", "J", "V", "S", "D"];
const monthKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const short = (n: number) => { const a = Math.abs(n); const s = a >= 1000 ? `${(a / 1000).toFixed(a >= 10000 ? 0 : 1)}k` : a.toFixed(0); return (n < 0 ? "−" : n > 0 ? "+" : "") + s; };
const hm = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", hour12: false }) : "—");
const dur = (a: string | null, b: string) => { if (!a) return "—"; const s = Math.max(0, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000)); return s < 60 ? `${s} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`; };
const fmtDay = (day: string) => new Date(day + "T12:00:00").toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long" });
const M = { l: 52, r: 12, t: 12, b: 24 };
/* Informe por duración: tramos como los diarios de trading */
const DUR: { label: string; max: number }[] = [
  { label: "0-15 s", max: 15 }, { label: "15-45 s", max: 45 }, { label: "45 s-1 m", max: 60 }, { label: "1-2 m", max: 120 }, { label: "2-5 m", max: 300 },
  { label: "5-10 m", max: 600 }, { label: "10-30 m", max: 1800 }, { label: "30 m-1 h", max: 3600 }, { label: "1-2 h", max: 7200 }, { label: "2-4 h", max: 14400 }, { label: "4 h+", max: Infinity },
];

export function Performance() {
  const { client, accounts, health, audit, density } = useStore();
  const compact = density === "compact";
  const [cursor, setCursor] = useState(() => { const d = new Date(); d.setDate(1); d.setHours(0, 0, 0, 0); return d; });
  const [account, setAccount] = useState("");
  const [data, setData] = useState<MonthStats | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [share, setShare] = useState(false);
  const [view, setView] = useState<"perf" | "news">(() => (window.location.hash.includes("news") ? "news" : "perf"));
  const [width, setWidth] = useState(600);
  const box = useRef<HTMLDivElement>(null);
  const [ddWidth, setDdWidth] = useState(500);
  const ddBox = useRef<HTMLDivElement>(null);
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
  useEffect(() => {
    const el = ddBox.current;
    if (!el) return;
    const ro = new ResizeObserver((e) => setDdWidth(Math.max(240, Math.floor(e[0].contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [data]);
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
    const longs = trades.filter((t) => t.side === "long"), shorts = trades.filter((t) => t.side === "short");
    const longPnl = longs.reduce((s, t) => s + t.pnl, 0), shortPnl = shorts.reduce((s, t) => s + t.pnl, 0);
    const winRate = trades.length ? wins.length / trades.length : null;
    const pf = gl > 0 ? gw / gl : gw > 0 ? Infinity : null;
    const avgWin = wins.length ? gw / wins.length : null, avgLoss = losses.length ? gl / losses.length : null;
    // Puntuación 0-100: aciertos (40), factor de beneficio hasta 3 (35) y ratio ganancia/pérdida media hasta 2 (25)
    const payoff = avgWin !== null && avgLoss ? avgWin / avgLoss : avgWin !== null ? 2 : null;
    const score = trades.length < 3 ? null : Math.round((winRate ?? 0) * 40 + Math.min(pf === null ? 0 : pf === Infinity ? 3 : pf, 3) / 3 * 35 + Math.min(payoff ?? 0, 2) / 2 * 25);
    return { net, fees, green, red, trades: trades.length, winRate, pf, avgWin, avgLoss, best, worst, streak, longs: longs.length, shorts: shorts.length, longPnl, shortPnl, payoff, score };
  }, [data, trades]);
  const durations = useMemo(() => DUR.map((b, i) => {
    const lo = i ? DUR[i - 1].max : 0;
    const ts = trades.filter((t) => { if (!t.opened_at) return false; const s = (new Date(t.closed_at).getTime() - new Date(t.opened_at).getTime()) / 1000; return s >= lo && s < b.max; });
    const w = ts.filter((t) => t.pnl > 0).length;
    return { ...b, n: ts.length, pnl: ts.reduce((s, t) => s + t.pnl, 0), win: ts.length ? w / ts.length : null };
  }), [trades]);
  const durMaxAbs = Math.max(1, ...durations.map((d) => Math.abs(d.pnl))), durMaxN = Math.max(1, ...durations.map((d) => d.n));

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
  const series = useMemo(() => { let acc = 0, peak = 0; return (data?.days ?? []).map((d) => { acc += d.net; peak = Math.max(peak, acc); return { day: d.day, net: d.net, cum: acc, dd: acc - peak }; }); }, [data]);
  const maxDd = series.reduce((m, s) => Math.min(m, s.dd), 0);
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
  const shareData: ShareData = { title: account ? `Resultados · ${label(account)}` : "Resultados del mes", period: cursor.toLocaleDateString("es-ES", { month: "long", year: "numeric" }),
    net: kpi.net, winRate: kpi.winRate, pf: kpi.pf, green: kpi.green, red: kpi.red, trades: kpi.trades, accounts: account ? 1 : (data?.accounts ?? []).length,
    bars: series.map((s) => ({ day: s.day, net: s.net })) };
  if (!client) return null;

  if (view === "news") {
    return (
      <div className="grid" data-testid="performance">
        <Card title="Calendario económico" icon="news" right={<div className="view-tabs" data-testid="view-tabs"><button onClick={() => setView("perf")}>Rendimiento</button><button className="active">Noticias</button></div>}>
          <NewsPanel client={client} />
        </Card>
      </div>
    );
  }
  return (
    <div className="grid" data-testid="performance">
      <Card>
        <div className="perf-bar">
          <div className="view-tabs" data-testid="view-tabs"><button className="active">Rendimiento</button><button onClick={() => setView("news")}>Noticias</button></div>
          <div className="month-nav">
            <button className="chip-btn" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1))} aria-label="Mes anterior">‹</button>
            <b>{cursor.toLocaleDateString("es-ES", { month: "long", year: "numeric" })}</b>
            <button className="chip-btn" onClick={() => setCursor(new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1))} aria-label="Mes siguiente">›</button>
            <button className="chip-btn" onClick={() => { const d = new Date(); d.setDate(1); d.setHours(0, 0, 0, 0); setCursor(d); }}>Hoy</button>
          </div>
          <div className="chips">
            <label className="inline">Cuenta
              <select value={account} onChange={(e) => setAccount(e.target.value)} data-testid="perf-account">
                <option value="">Todas</option>
                {accountOptions.map((id) => <option key={id} value={id}>{label(id)}{id === master ? " · maestra" : ""}</option>)}
              </select>
            </label>
            <button className="primary small-btn share-btn" onClick={() => setShare(true)} disabled={!data || data.days.length === 0} data-testid="share-open"><Icon name="share" size={14} /> Compartir</button>
          </div>
        </div>
      </Card>
      {share && <ShareModal data={shareData} onClose={() => setShare(false)} />}

      <div className="kpis" data-testid="perf-kpis">
        <Kpi label="P&L del mes" icon="wallet" value={moneyShort(kpi.net)} title={signedMoney(kpi.net)} tone={kpi.net > 0 ? "ok" : kpi.net < 0 ? "bad" : "muted"}
             sub={kpi.fees ? `neto · comisiones ${moneyShort(kpi.fees).replace("+", "")}` : "sin comisiones anotadas"} />
        <Kpi label="Días verdes" icon="sun" value={`${kpi.green} / ${kpi.green + kpi.red}`} tone={kpi.green + kpi.red ? (kpi.green >= kpi.red ? "ok" : "bad") : "muted"}
             ring={kpi.green + kpi.red ? (100 * kpi.green) / (kpi.green + kpi.red) : null} sub={kpi.streak ? `racha: ${Math.abs(kpi.streak)} ${kpi.streak > 0 ? "verdes" : "rojos"}` : "verdes / operados"} />
        <Kpi label="Aciertos" icon="target" value={kpi.winRate === null ? "—" : `${Math.round(kpi.winRate * 100)} %`} tone={kpi.winRate === null ? "muted" : kpi.winRate >= 0.5 ? "ok" : "warn"}
             ring={kpi.winRate === null ? null : kpi.winRate * 100} sub={`${kpi.trades} operaciones`} />
        <Kpi label="Factor de beneficio" icon="scale" value={kpi.pf === null ? "—" : kpi.pf === Infinity ? "∞" : kpi.pf.toFixed(2)} tone={kpi.pf === null ? "muted" : kpi.pf >= 1.5 ? "ok" : kpi.pf >= 1 ? "warn" : "bad"} sub="ganado ÷ perdido" />
        <Kpi label="Media gan. / perd." icon="trendUp" value={<>{kpi.avgWin === null ? "—" : <span className="ok">{moneyShort(kpi.avgWin)}</span>} <span className="muted">/</span> {kpi.avgLoss === null ? "—" : <span className="bad">{moneyShort(-kpi.avgLoss)}</span>}</>}
             bar={[kpi.avgWin ?? 0, kpi.avgLoss ?? 0]} sub={kpi.payoff !== null ? `ratio ${kpi.payoff.toFixed(2)}` : "por operación"} />
        <Kpi label="Largos / cortos" icon="layers" value={<><span className={kpi.longPnl > 0 ? "ok" : kpi.longPnl < 0 ? "bad" : ""}>{moneyShort(kpi.longPnl)}</span> <span className="muted">/</span> <span className={kpi.shortPnl > 0 ? "ok" : kpi.shortPnl < 0 ? "bad" : ""}>{moneyShort(kpi.shortPnl)}</span></>}
             sub={`${kpi.longs} largos · ${kpi.shorts} cortos`} />
        <Kpi label="Mejor día" icon="trophy" value={kpi.best ? moneyShort(kpi.best.net) : "—"} tone={kpi.best ? "ok" : "muted"} sub={kpi.best ? fmtDay(kpi.best.day) : ""} />
        <Kpi label="Peor día" icon="thumbDown" value={kpi.worst && kpi.worst.net < 0 ? moneyShort(kpi.worst.net) : "—"} tone={kpi.worst && kpi.worst.net < 0 ? "bad" : "muted"} sub={kpi.worst && kpi.worst.net < 0 ? fmtDay(kpi.worst.day) : "ningún día en rojo"} />
      </div>

      <div className="perf-charts">
        <Card title="Puntuación del mes" icon="gauge" right={<span className="muted small">aciertos 40 · factor de beneficio 35 · ratio ganancia/pérdida 25</span>}>
          <div className="score">
            <div>
              <div className="score-num"><span className={kpi.score === null ? "muted" : kpi.score >= 70 ? "ok" : kpi.score >= 45 ? "warn" : "bad"}>{kpi.score ?? "—"}</span><small> / 100</small></div>
              <div className="score-bar"><i style={{ left: `${kpi.score ?? 0}%` }} /></div>
              <div className="score-axes">
                <span>Aciertos <b>{kpi.winRate === null ? "—" : `${Math.round(kpi.winRate * 100)} %`}</b> · factor <b>{kpi.pf === null ? "—" : kpi.pf === Infinity ? "∞" : kpi.pf.toFixed(2)}</b> · ratio gan./pérd. <b>{kpi.payoff === null ? "—" : kpi.payoff.toFixed(2)}</b></span>
                <span>{kpi.score === null ? "Hacen falta al menos 3 operaciones cerradas." : kpi.score >= 70 ? "Mes sólido: sigue con el mismo plan." : kpi.score >= 45 ? "Mes aceptable: revisa las operaciones perdedoras más largas." : "Mes flojo: reduce tamaño y protege el drawdown."}</span>
              </div>
            </div>
            <svg width="120" height="110" viewBox="0 0 120 110" role="img" aria-label="Aciertos, factor y ratio">
              {[1, 0.66, 0.33].map((k) => <polygon key={k} points={`${60},${55 - 45 * k} ${60 + 45 * k * 0.87},${55 + 45 * k * 0.5} ${60 - 45 * k * 0.87},${55 + 45 * k * 0.5}`} fill="none" stroke="var(--line-2)" />)}
              {(() => { const a = (kpi.winRate ?? 0), b = Math.min(kpi.pf === null ? 0 : kpi.pf === Infinity ? 3 : kpi.pf, 3) / 3, c = Math.min(kpi.payoff ?? 0, 2) / 2;
                return <polygon points={`${60},${55 - 45 * a} ${60 + 45 * b * 0.87},${55 + 45 * b * 0.5} ${60 - 45 * c * 0.87},${55 + 45 * c * 0.5}`} fill="rgba(56,189,248,.25)" stroke="var(--accent)" strokeWidth={2} />; })()}
              <text x="60" y="8" textAnchor="middle" className="tick" fill="var(--muted)" fontSize="9">aciertos</text>
              <text x="112" y="88" textAnchor="end" className="tick" fill="var(--muted)" fontSize="9">factor</text>
              <text x="8" y="88" textAnchor="start" className="tick" fill="var(--muted)" fontSize="9">ratio</text>
            </svg>
          </div>
        </Card>
        <Card title="Drawdown del mes" icon="trendDown" right={<span className="muted small">caída desde el máximo del acumulado</span>}>
          {series.length === 0 ? <Empty>Sin días operados.</Empty> : (
            <div className="chart-wrap perf-chart" ref={ddBox}>
              <svg viewBox={`0 0 ${ddWidth} ${compact ? 120 : 150}`} width={ddWidth} height={compact ? 120 : 150} role="img" aria-label="Drawdown del mes">
                {(() => { const h = compact ? 120 : 150, ih2 = h - M.t - M.b, lo2 = Math.min(maxDd, -1) * 1.1, iw2 = ddWidth - M.l - M.r;
                  const x2 = (i: number) => M.l + ((i + 0.5) / Math.max(1, series.length)) * iw2;
                  const y2 = (v: number) => M.t + ((0 - v) / (0 - lo2)) * ih2;
                  const pts = series.map((s, i) => `${x2(i).toFixed(1)},${y2(s.dd).toFixed(1)}`).join(" ");
                  return <>
                    <line x1={M.l} x2={ddWidth - M.r} y1={y2(0)} y2={y2(0)} className="zero" />
                    <text x={M.l - 8} y={y2(0) + 4} textAnchor="end" className="tick">0</text>
                    <text x={M.l - 8} y={y2(lo2) + 4} textAnchor="end" className="tick">{Math.abs(lo2) >= 1000 ? `${(lo2 / 1000).toFixed(1)}k` : Math.round(lo2)}</text>
                    <polygon points={`${x2(0).toFixed(1)},${y2(0).toFixed(1)} ${pts} ${x2(series.length - 1).toFixed(1)},${y2(0).toFixed(1)}`} className="dd-area" />
                    <polyline points={pts} className="dd-line" />
                    {series.map((s, i) => (i % Math.max(1, Math.ceil(series.length / 6)) === 0) && <text key={s.day} x={x2(i)} y={h - 6} textAnchor="middle" className="tick">{Number(s.day.slice(8))}</text>)}
                    <text x={ddWidth - M.r} y={M.t + 10} textAnchor="end" className="tick bad">máx. {signedMoney(maxDd)}</text>
                  </>; })()}
              </svg>
            </div>
          )}
        </Card>
      </div>

      <Card title="Calendario" icon="calendar" right={<span className="muted small">{account ? label(account) : `todas · ${(data?.accounts ?? []).length} cuentas`} · P&L del bróker por día; toca un día para ver sus operaciones</span>}>
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
        <Card title={`Detalle · ${fmtDay(selected)}`} icon="list" right={<button className="chip-btn" onClick={() => setSelected(null)}>Cerrar</button>}>
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

      <Card title="Evolución del mes" icon="trendUp" right={<span className="muted small">barras: P&L neto de cada día · línea: acumulado</span>}>
        <div className="chart-wrap perf-chart" ref={box}>
          {series.length === 0 ? <Empty>Sin días operados en este mes.</Empty> : (
            <>
              <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="P&L diario y acumulado del mes" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
                <defs><linearGradient id="pnl-area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--accent)" stopOpacity={0.28} /><stop offset="1" stopColor="var(--accent)" stopOpacity={0} /></linearGradient></defs>
                {series.length > 1 && <path d={`M${x(0).toFixed(1)},${y(0).toFixed(1)} ` + series.map((s, i) => `L${x(i).toFixed(1)},${y(s.cum).toFixed(1)}`).join(" ") + ` L${x(series.length - 1).toFixed(1)},${y(0).toFixed(1)} Z`} className="area" />}
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

      <Card title="Por duración de la operación" icon="clock" right={<span className="muted small">¿en qué tramos ganas y en cuáles pierdes? · solo operaciones con hora de entrada</span>}>
        {trades.some((t) => t.opened_at) ? (
          <div className="dur-grid" data-testid="duration-report">
            <div>
              <div className="muted small chart-title">P&L por tramo</div>
              <div className="hbars">{durations.map((d) => (
                <div key={d.label} className="hbar"><span className="lbl">{d.label}</span>
                  <span className="track"><span className="mid" style={{ left: "50%" }} />{d.n > 0 && <i className={d.pnl >= 0 ? "pos" : "neg"} style={{ left: d.pnl >= 0 ? "50%" : `${50 - (50 * Math.abs(d.pnl)) / durMaxAbs}%`, width: `${(50 * Math.abs(d.pnl)) / durMaxAbs}%` }} />}</span>
                  <span className={`val ${d.pnl > 0 ? "ok" : d.pnl < 0 ? "bad" : "muted"}`}>{d.n ? moneyShort(d.pnl) : "—"}</span></div>
              ))}</div>
            </div>
            <div>
              <div className="muted small chart-title">Operaciones por tramo</div>
              <div className="hbars">{durations.map((d) => (
                <div key={d.label} className="hbar"><span className="lbl">{d.label}</span>
                  <span className="track">{d.n > 0 && <i style={{ left: 0, width: `${(100 * d.n) / durMaxN}%` }} />}</span>
                  <span className="val">{d.n || "—"}</span></div>
              ))}</div>
            </div>
            <div>
              <div className="muted small chart-title">Aciertos por tramo</div>
              <div className="hbars">{durations.map((d) => (
                <div key={d.label} className="hbar"><span className="lbl">{d.label}</span>
                  <span className="track"><span className="mid" style={{ left: "50%" }} />{d.win !== null && <i className={d.win >= 0.5 ? "pos" : "neg"} style={{ left: 0, width: `${100 * d.win}%` }} />}</span>
                  <span className={`val ${d.win === null ? "muted" : d.win >= 0.5 ? "ok" : "warn"}`}>{d.win === null ? "—" : `${Math.round(d.win * 100)} %`}</span></div>
              ))}</div>
            </div>
          </div>
        ) : <Empty>Aún no hay operaciones con hora de entrada este mes.</Empty>}
      </Card>
    </div>
  );
}
