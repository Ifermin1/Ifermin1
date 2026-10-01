/* Estadísticas de trading sobre las operaciones cerradas que reconstruye el engine y sobre los días.
 * Funciones puras (sin React) para usarlas en Inicio, Calendario y Análisis con las mismas definiciones. */

export type TradeLike = { id: number; account_id: string; symbol: string; side: "long" | "short"; quantity: number; pnl: number; commissions: number;
                          opened_at: string | null; closed_at: string; day: string };
export type DayLike = { day: string; net: number };

export type TradeStats = {
  n: number; wins: number; losses: number; be: number; winRate: number | null;
  gross: number; fees: number; net: number; grossProfit: number; grossLoss: number;
  pf: number | null;                 // factor de beneficio: ganado ÷ perdido (∞ sin pérdidas)
  expectancy: number | null;         // esperanza matemática: neto medio por operación
  avgWin: number | null; avgLoss: number | null; payoff: number | null;   // media ganadora, media perdedora y su ratio
  largestWin: number; largestLoss: number;
  maxWinStreak: number; maxLossStreak: number; currentStreak: number;     // racha actual: + ganadoras, − perdedoras
  stdDev: number | null;             // desviación típica del resultado por operación
  rExpectancy: number | null;        // esperanza en R (neto medio ÷ pérdida media)
  avgDurWin: number | null; avgDurLoss: number | null;                     // segundos
  kelly: number | null;              // fracción de Kelly: W − (1−W)/payoff
};
export type DayStats = {
  n: number; green: number; red: number; net: number; avg: number | null; best: DayLike | null; worst: DayLike | null;
  maxDd: number; maxDdPct: number | null; peak: number; equity: { day: string; cum: number; dd: number }[];
  dailyStd: number | null; sharpe: number | null; sortino: number | null; calmar: number | null; streak: number;
};
export type Bucket = { key: string; label: string; n: number; net: number; wins: number; winRate: number | null };

const sum = (xs: number[]) => xs.reduce((s, x) => s + x, 0);
const mean = (xs: number[]) => (xs.length ? sum(xs) / xs.length : null);
const std = (xs: number[]) => { const m = mean(xs); if (m === null || xs.length < 2) return null; return Math.sqrt(sum(xs.map((x) => (x - m) ** 2)) / (xs.length - 1)); };
export const seconds = (t: TradeLike): number | null => (t.opened_at ? Math.max(0, Math.round((new Date(t.closed_at).getTime() - new Date(t.opened_at).getTime()) / 1000)) : null);
export const netOf = (t: TradeLike) => Math.round(((t.pnl ?? 0) - (t.commissions ?? 0)) * 100) / 100;

/** Estadísticas por operación. `value` decide si se mide en bruto o en neto; `be` = tolerancia para breakeven. */
export function tradeStats(trades: TradeLike[], value: (t: TradeLike) => number = netOf, be = 0): TradeStats {
  const vals = trades.map(value);
  const wins = trades.filter((t, i) => vals[i] > be), losses = trades.filter((t, i) => vals[i] < -be);
  const wv = wins.map(value), lv = losses.map(value);
  const grossProfit = sum(wv), grossLoss = -sum(lv);
  const gross = sum(trades.map((t) => t.pnl ?? 0)), fees = sum(trades.map((t) => t.commissions ?? 0));
  const avgWin = mean(wv), avgLoss = lv.length ? grossLoss / lv.length : null;
  const payoff = avgWin !== null && avgLoss ? avgWin / avgLoss : null;
  const winRate = trades.length ? wins.length / trades.length : null;
  let maxW = 0, maxL = 0, cur = 0;
  for (const v of [...trades].sort((a, b) => a.closed_at.localeCompare(b.closed_at)).map(value)) {
    if (v > be) { cur = cur > 0 ? cur + 1 : 1; maxW = Math.max(maxW, cur); }
    else if (v < -be) { cur = cur < 0 ? cur - 1 : -1; maxL = Math.max(maxL, -cur); }
  }
  const durW = wins.map(seconds).filter((s): s is number => s !== null), durL = losses.map(seconds).filter((s): s is number => s !== null);
  const expectancy = mean(vals);
  return { n: trades.length, wins: wins.length, losses: losses.length, be: trades.length - wins.length - losses.length, winRate,
           gross, fees, net: gross - fees, grossProfit, grossLoss,
           pf: grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : null,
           expectancy, avgWin, avgLoss, payoff, largestWin: wv.length ? Math.max(...wv) : 0, largestLoss: lv.length ? Math.min(...lv) : 0,
           maxWinStreak: maxW, maxLossStreak: maxL, currentStreak: cur, stdDev: std(vals),
           rExpectancy: expectancy !== null && avgLoss ? expectancy / avgLoss : null,
           avgDurWin: mean(durW), avgDurLoss: mean(durL),
           kelly: winRate !== null && payoff ? winRate - (1 - winRate) / payoff : null };
}

/** Estadísticas por día: curva de capital acumulada, drawdown máximo, Sharpe/Sortino/Calmar anualizados (252 sesiones). */
export function dayStats(days: DayLike[]): DayStats {
  const ds = [...days].sort((a, b) => a.day.localeCompare(b.day));
  let cum = 0, peak = 0, maxDd = 0, peakAtMaxDd = 0;
  const equity = ds.map((d) => { cum += d.net; if (cum > peak) peak = cum; const dd = cum - peak; if (dd < maxDd) { maxDd = dd; peakAtMaxDd = peak; } return { day: d.day, cum: Math.round(cum * 100) / 100, dd: Math.round(dd * 100) / 100 }; });
  const nets = ds.map((d) => d.net);
  const m = mean(nets), s = std(nets);
  const downside = nets.filter((x) => x < 0);
  const dsd = downside.length ? Math.sqrt(sum(downside.map((x) => x * x)) / nets.length) : null;
  const best = ds.reduce<DayLike | null>((b, d) => (b === null || d.net > b.net ? d : b), null);
  const worst = ds.reduce<DayLike | null>((b, d) => (b === null || d.net < b.net ? d : b), null);
  let streak = 0;
  for (const d of [...ds].reverse()) { if (d.net === 0) continue; const sg = d.net > 0 ? 1 : -1; if (streak === 0) streak = sg; else if (Math.sign(streak) === sg) streak += sg; else break; }
  const annual = m !== null ? m * 252 : null;
  return { n: ds.length, green: ds.filter((d) => d.net > 0).length, red: ds.filter((d) => d.net < 0).length, net: sum(nets), avg: m, best, worst,
           maxDd: -maxDd, maxDdPct: peakAtMaxDd > 0 ? -maxDd / peakAtMaxDd : null, peak, equity, dailyStd: s,
           sharpe: m !== null && s ? (m / s) * Math.sqrt(252) : null,
           sortino: m !== null && dsd ? (m / dsd) * Math.sqrt(252) : null,
           calmar: annual !== null && maxDd < 0 ? annual / -maxDd : null, streak };
}

const bucketize = (trades: TradeLike[], keyOf: (t: TradeLike) => string, labelOf: (k: string) => string, value: (t: TradeLike) => number, order?: string[]): Bucket[] => {
  const m = new Map<string, Bucket>();
  for (const t of trades) {
    const k = keyOf(t); const v = value(t);
    const b = m.get(k) ?? { key: k, label: labelOf(k), n: 0, net: 0, wins: 0, winRate: null };
    b.n += 1; b.net = Math.round((b.net + v) * 100) / 100; b.wins += v > 0 ? 1 : 0; m.set(k, b);
  }
  const out = (order ?? [...m.keys()].sort()).map((k) => m.get(k) ?? { key: k, label: labelOf(k), n: 0, net: 0, wins: 0, winRate: null });
  for (const b of out) b.winRate = b.n ? b.wins / b.n : null;
  return out;
};
const HOURS = Array.from({ length: 24 }, (_, h) => String(h));
export const byHour = (trades: TradeLike[], value = netOf) => {
  const all = bucketize(trades, (t) => String(new Date(t.opened_at ?? t.closed_at).getHours()), (k) => `${k.padStart(2, "0")}h`, value, HOURS);
  const used = all.map((b, i) => (b.n ? i : -1)).filter((i) => i >= 0);
  return used.length ? all.slice(Math.min(...used), Math.max(...used) + 1) : [];   // de la primera hora operada a la última
};
const WD = ["1", "2", "3", "4", "5", "6", "0"], WD_LABEL: Record<string, string> = { "1": "Lun", "2": "Mar", "3": "Mié", "4": "Jue", "5": "Vie", "6": "Sáb", "0": "Dom" };
export const byWeekday = (trades: TradeLike[], value = netOf) =>
  bucketize(trades, (t) => String(new Date(t.day + "T12:00:00").getDay()), (k) => WD_LABEL[k], value, WD).filter((b) => b.n > 0 || !["6", "0"].includes(b.key));
export const bySymbol = (trades: TradeLike[], value = netOf) => bucketize(trades, (t) => t.symbol.split(" ")[0].toUpperCase(), (k) => k, value).sort((a, b) => b.n - a.n);
export const byAccount = (trades: TradeLike[], value = netOf) => bucketize(trades, (t) => t.account_id, (k) => k, value).sort((a, b) => b.net - a.net);
export const bySide = (trades: TradeLike[], value = netOf) => bucketize(trades, (t) => t.side, (k) => (k === "long" ? "Largos" : "Cortos"), value, ["long", "short"]);

/** Histograma del resultado por operación: contenedores de anchura "bonita" centrados en 0. */
export function histogram(trades: TradeLike[], value = netOf, bins = 15): { from: number; to: number; n: number }[] {
  const vals = trades.map(value);
  if (!vals.length) return [];
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const raw = Math.max(1, (hi - lo) / bins);
  const p = 10 ** Math.floor(Math.log10(raw)); const mlt = raw / p;
  const w = (mlt < 1.5 ? 1 : mlt < 3.5 ? 2.5 : mlt < 7.5 ? 5 : 10) * p;
  const start = Math.floor(lo / w) * w, end = Math.ceil((hi + 1e-9) / w) * w;
  const out: { from: number; to: number; n: number }[] = [];
  for (let x = start; x < end; x += w) out.push({ from: Math.round(x * 100) / 100, to: Math.round((x + w) * 100) / 100, n: 0 });
  for (const v of vals) { const i = Math.min(out.length - 1, Math.floor((v - start) / w)); out[i].n += 1; }
  return out;
}
