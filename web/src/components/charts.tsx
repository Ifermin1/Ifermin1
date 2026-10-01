import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Bucket } from "../lib/stats";

/* Gráficas SVG ligeras con los tokens del tema: barras con signo por categoría, histograma, curva de capital con
 * drawdown y una línea pequeña (sparkline). Todas con pop-up al pasar el cursor y sin dependencias. */

export function useWidth<T extends HTMLElement>(initial = 600): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(initial);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(Math.max(200, Math.floor(e[0].contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}
const fmt = (n: number) => `${n < 0 ? "−" : n > 0 ? "+" : ""}${Math.abs(n).toLocaleString("es-ES", { maximumFractionDigits: Math.abs(n) >= 100 ? 0 : 2 })} $`;
const niceTicks = (lo: number, hi: number, n = 4): number[] => { const span = hi - lo || 1; const raw = span / n; const p = 10 ** Math.floor(Math.log10(raw)); const m = raw / p; const st = (m < 1.5 ? 1 : m < 3.5 ? 2.5 : m < 7.5 ? 5 : 10) * p; const t: number[] = []; for (let v = Math.ceil(lo / st) * st; v <= hi + 1e-9; v += st) t.push(Math.round(v * 1000) / 1000); return t; };

export function Tip({ x, y, w, children }: { x: number; y: number; w: number; children: ReactNode }) {
  return <div className="chart-tip" style={{ left: Math.min(w - 180, Math.max(0, x - 85)), top: Math.max(0, y) }}>{children}</div>;
}

/** Barras con signo por categoría (hora, día de la semana, símbolo, cuenta): neto por barra y aciertos en el pop-up. */
export function SignedBars({ data, height = 180, label = "neto", testId, showN = true }: { data: Bucket[]; height?: number; label?: string; testId?: string; showN?: boolean }) {
  const [box, W] = useWidth<HTMLDivElement>();
  const [hov, setHov] = useState<number | null>(null);
  const M = { l: 48, r: 8, t: 10, b: 24 }, H = height, iw = W - M.l - M.r, ih = H - M.t - M.b;
  const vals = data.map((d) => d.net);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  if (hi - lo < 1) { hi += 50; lo -= 50; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const y = (v: number) => M.t + (1 - (v - lo) / (hi - lo)) * ih;
  const bw = Math.max(3, Math.min(42, (iw / Math.max(1, data.length)) * 0.62));
  const x = (i: number) => M.l + ((i + 0.5) / Math.max(1, data.length)) * iw;
  const step = Math.max(1, Math.ceil(data.length / Math.max(2, Math.floor(iw / 44))));
  const h = hov !== null ? data[hov] : null;
  return (
    <div ref={box} className="chart-box" data-testid={testId}>
      {data.length === 0 ? <p className="empty">Sin datos.</p> : (
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label={`${label} por categoría`}>
          {niceTicks(lo, hi).map((v) => <g key={v}><line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className={v === 0 ? "zero" : "grid"} /><text x={M.l - 6} y={y(v) + 4} textAnchor="end" className="tick">{Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(v % 1000 ? 1 : 0)}k` : v}</text></g>)}
          {data.map((d, i) => (
            <g key={d.key} onPointerEnter={() => setHov(i)} onPointerLeave={() => setHov(null)}>
              <rect x={x(i) - bw / 2} y={Math.min(y(0), y(d.net))} width={bw} height={Math.max(1, Math.abs(y(d.net) - y(0)))} rx={3} className={`bar ${d.net > 0 ? "pos" : d.net < 0 ? "neg" : "flat"} ${hov === i ? "hov" : ""}`} />
              <rect x={x(i) - (iw / Math.max(1, data.length)) / 2} y={M.t} width={iw / Math.max(1, data.length)} height={ih} fill="transparent" />
              {i % step === 0 && <text x={x(i)} y={H - 7} textAnchor="middle" className="tick">{d.label}</text>}
            </g>
          ))}
        </svg>
      )}
      {h && <Tip x={x(hov!)} y={Math.min(y(h.net), y(0)) - 64} w={W}><b>{h.label}</b><span className={h.net > 0 ? "ok" : h.net < 0 ? "bad" : ""}>{fmt(h.net)} {label}</span>{showN && <span className="muted">{h.n} operaciones{h.winRate !== null ? ` · ${Math.round(h.winRate * 100)} % ganadoras` : ""}</span>}</Tip>}
    </div>
  );
}

/** Histograma del resultado por operación. */
export function HistogramChart({ bins, height = 180, testId }: { bins: { from: number; to: number; n: number }[]; height?: number; testId?: string }) {
  const [box, W] = useWidth<HTMLDivElement>();
  const [hov, setHov] = useState<number | null>(null);
  const M = { l: 36, r: 8, t: 10, b: 24 }, H = height, iw = W - M.l - M.r, ih = H - M.t - M.b;
  const max = Math.max(1, ...bins.map((b) => b.n));
  const y = (n: number) => M.t + (1 - n / (max * 1.08)) * ih;
  const x = (i: number) => M.l + (i / Math.max(1, bins.length)) * iw;
  const bw = iw / Math.max(1, bins.length);
  const step = Math.max(1, Math.ceil(bins.length / Math.max(2, Math.floor(iw / 56))));
  const h = hov !== null ? bins[hov] : null;
  return (
    <div ref={box} className="chart-box" data-testid={testId}>
      {bins.length === 0 ? <p className="empty">Sin datos.</p> : (
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Distribución del resultado por operación">
          {niceTicks(0, max, 3).map((v) => <g key={v}><line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className="grid" /><text x={M.l - 6} y={y(v) + 4} textAnchor="end" className="tick">{v}</text></g>)}
          {bins.map((b, i) => (
            <g key={i} onPointerEnter={() => setHov(i)} onPointerLeave={() => setHov(null)}>
              <rect x={x(i) + 1} y={y(b.n)} width={Math.max(1, bw - 2)} height={Math.max(0, y(0) - y(b.n))} rx={2} className={`bar ${b.to <= 0 ? "neg" : b.from >= 0 ? "pos" : "flat"} ${hov === i ? "hov" : ""}`} />
              {i % step === 0 && <text x={x(i)} y={H - 7} textAnchor="middle" className="tick">{b.from >= 1000 || b.from <= -1000 ? `${(b.from / 1000).toFixed(1)}k` : b.from}</text>}
            </g>
          ))}
          <line x1={M.l} x2={W - M.r} y1={y(0)} y2={y(0)} className="zero" />
        </svg>
      )}
      {h && <Tip x={x(hov!) + bw / 2} y={y(h.n) - 56} w={W}><b>{fmt(h.from)} a {fmt(h.to)}</b><span>{h.n} operaciones</span></Tip>}
    </div>
  );
}

/** Curva de capital acumulada con el drawdown sombreado debajo del máximo. */
export function EquityCurve({ points, height = 220, testId, compact }: { points: { day: string; cum: number; dd: number }[]; height?: number; testId?: string; compact?: boolean }) {
  const [box, W] = useWidth<HTMLDivElement>();
  const [hov, setHov] = useState<number | null>(null);
  const M = { l: compact ? 40 : 52, r: 10, t: 10, b: compact ? 18 : 24 }, H = height, iw = W - M.l - M.r, ih = H - M.t - M.b;
  const vals = points.flatMap((p) => [p.cum, p.cum - p.dd]);
  let lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  if (hi - lo < 1) { hi += 100; lo -= 100; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const x = (i: number) => M.l + ((i + 0.5) / Math.max(1, points.length)) * iw;
  const y = (v: number) => M.t + (1 - (v - lo) / (hi - lo)) * ih;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join(" ");
  const peakLine = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.cum - p.dd).toFixed(1)}`).join(" ");
  const ddArea = points.length ? `${peakLine} ${[...points].reverse().map((p, k) => `L${x(points.length - 1 - k).toFixed(1)},${y(p.cum).toFixed(1)}`).join(" ")} Z` : "";
  const area = points.length ? `${line} L${x(points.length - 1).toFixed(1)},${y(0).toFixed(1)} L${x(0).toFixed(1)},${y(0).toFixed(1)} Z` : "";
  const step = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(iw / 70))));
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => { const r = e.currentTarget.getBoundingClientRect(); const px = ((e.clientX - r.left) / r.width) * W; if (px < M.l || px > W - M.r || !points.length) { setHov(null); return; } setHov(Math.min(points.length - 1, Math.max(0, Math.floor(((px - M.l) / iw) * points.length)))); };
  const h = hov !== null ? points[hov] : null;
  const fmtDay = (d: string) => new Date(d + "T12:00:00").toLocaleDateString("es-ES", { day: "2-digit", month: "short" }).replace(".", "");
  return (
    <div ref={box} className="chart-box" data-testid={testId}>
      {points.length === 0 ? <p className="empty">Sin días con resultado.</p> : (
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="chart equity" role="img" aria-label="Curva de capital acumulada y drawdown" onPointerMove={onMove} onPointerLeave={() => setHov(null)}>
          {niceTicks(lo, hi).map((v) => <g key={v}><line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className={v === 0 ? "zero" : "grid"} /><text x={M.l - 6} y={y(v) + 4} textAnchor="end" className="tick">{Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(Math.abs(v) >= 10000 ? 0 : 1)}k` : v}</text></g>)}
          <path d={area} className="eq-area" />
          <path d={ddArea} className="dd-area" />
          <path d={peakLine} className="peak-line" />
          <path d={line} className="eq-line" />
          {points.map((p, i) => i % step === 0 && <text key={p.day} x={x(i)} y={H - 6} textAnchor="middle" className="tick">{fmtDay(p.day)}</text>)}
          {h && <><line x1={x(hov!)} x2={x(hov!)} y1={M.t} y2={H - M.b} className="cross" /><circle cx={x(hov!)} cy={y(h.cum)} r={4} className="eq-dot" /></>}
        </svg>
      )}
      {h && <Tip x={x(hov!)} y={4} w={W}><b>{fmtDay(h.day)}</b><span>Acumulado: <b className={h.cum >= 0 ? "ok" : "bad"}>{fmt(h.cum)}</b></span>{h.dd < 0 && <span className="bad">Drawdown: {fmt(h.dd)}</span>}</Tip>}
    </div>
  );
}

/** Línea pequeña para una tarjeta (sin ejes). */
export function Sparkline({ values, height = 36, width = 120 }: { values: number[]; height?: number; width?: number }) {
  if (values.length < 2) return <svg width={width} height={height} />;
  const lo = Math.min(0, ...values), hi = Math.max(0, ...values);
  const y = (v: number) => height - 2 - ((v - lo) / (hi - lo || 1)) * (height - 4);
  const x = (i: number) => (i / (values.length - 1)) * width;
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = values[values.length - 1];
  return <svg width={width} height={height} className="spark" aria-hidden="true"><line x1={0} x2={width} y1={y(0)} y2={y(0)} className="zero" /><path d={d} className={last >= 0 ? "ok" : "bad"} /></svg>;
}
