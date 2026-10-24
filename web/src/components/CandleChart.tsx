import { useEffect, useMemo, useRef, useState } from "react";
import type { Client, WorkingOrder } from "../lib/api";
import { tickSize } from "../lib/format";
import { useWidth } from "./charts";

/* Gráfico de velas de 1 minuto (las construye el engine con los ticks del addon) con las líneas de la posición: entrada,
 * stop(s) y objetivo(s). El stop y el objetivo se pueden ARRASTRAR: al soltar se manda la modificación (Proteger). */
type Bar = { t: string; o: number; h: number; l: number; c: number; v: number; m: number };
type Line = { kind: "entry" | "stop" | "target" | "last"; price: number; label: string; orderId?: string };

export function CandleChart({ client, symbol, account, quantity, avg, stops, targets, disabled, onMoved }: {
  client: Client; symbol: string; account?: string | null; quantity?: number; avg?: number | null; stops?: WorkingOrder[]; targets?: WorkingOrder[];
  disabled?: boolean; onMoved?: (msg: string) => void }) {
  const [box, W] = useWidth<HTMLDivElement>();
  const [bars, setBars] = useState<Bar[]>([]);
  const [last, setLast] = useState<number | null>(null);
  const [drag, setDrag] = useState<{ kind: "stop" | "target"; price: number; from: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [n, setN] = useState(60);
  const dragRef = useRef(drag);
  dragRef.current = drag;
  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    const load = () => client.bars(symbol, n).then((r) => { if (alive) { setBars(r.bars); setLast(r.last); } }).catch(() => {});
    load();
    const t = window.setInterval(load, 5000);
    return () => { alive = false; window.clearInterval(t); };
  }, [client, symbol, n]);
  const tick = tickSize(symbol);
  const H = 260, M = { l: 8, r: 64, t: 10, b: 22 };
  const iw = Math.max(50, W - M.l - M.r), ih = H - M.t - M.b;
  const lines: Line[] = useMemo(() => {
    const out: Line[] = [];
    if (avg && quantity) out.push({ kind: "entry", price: avg, label: `entrada ${quantity > 0 ? "L" : "C"}${Math.abs(quantity)}` });
    for (const o of stops ?? []) out.push({ kind: "stop", price: o.stop_price, label: `stop ${o.quantity}`, orderId: o.order_id });
    for (const o of targets ?? []) out.push({ kind: "target", price: o.limit_price, label: `obj. ${o.quantity}`, orderId: o.order_id });
    if (last) out.push({ kind: "last", price: last, label: "último" });
    return out;
  }, [avg, quantity, stops, targets, last]);
  const visible = bars.slice(-n);
  const prices = [...visible.flatMap((b) => [b.h, b.l]), ...lines.map((l) => l.price), ...(drag ? [drag.price] : [])].filter((x) => x > 0);
  const empty = !visible.length && !prices.length;
  let lo = empty ? 0 : Math.min(...prices), hi = empty ? 1 : Math.max(...prices);
  if (hi - lo < tick * 8) { lo -= tick * 4; hi += tick * 4; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const y = (p: number) => M.t + (1 - (p - lo) / (hi - lo)) * ih;
  const py = (yy: number) => lo + (1 - (yy - M.t) / ih) * (hi - lo);
  const bw = Math.max(2, Math.min(14, (iw / Math.max(1, visible.length)) * 0.7));
  const x = (i: number) => M.l + ((i + 0.5) / Math.max(1, visible.length)) * iw;
  const dec = tick >= 1 ? 0 : tick >= 0.1 ? 1 : tick >= 0.01 ? 2 : 4;
  const fmt = (p: number) => p.toFixed(dec);
  const ticks = useMemo(() => { const span = hi - lo; if (!(span > 0)) return []; const raw = span / 5; const p = 10 ** Math.floor(Math.log10(raw)); const m = raw / p; const st = (m < 1.5 ? 1 : m < 3.5 ? 2.5 : m < 7.5 ? 5 : 10) * p; const t: number[] = []; for (let v = Math.ceil(lo / st) * st; v <= hi && t.length < 40; v += st) t.push(v); return t; }, [lo, hi]);
  if (empty) return <div ref={box} className="chart-box"><p className="empty">Sin velas todavía: llegan con los ticks del addon (en el simulador, en unos segundos).</p></div>;
  const snap = (p: number) => Math.round(p / tick) * tick;
  const onDown = (l: Line) => (e: React.PointerEvent<SVGGElement>) => {
    if (disabled || !account || !quantity || (l.kind !== "stop" && l.kind !== "target")) return;
    (e.currentTarget.ownerSVGElement as SVGSVGElement).setPointerCapture?.(e.pointerId);
    setDrag({ kind: l.kind, price: l.price, from: l.price });
  };
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!dragRef.current) return;
    const r = e.currentTarget.getBoundingClientRect();
    const yy = ((e.clientY - r.top) / r.height) * H;
    setDrag({ ...dragRef.current, price: snap(py(yy)) });
  };
  const onUp = async () => {
    const d = dragRef.current;
    if (!d) return;
    setDrag(null);
    if (!account || !symbol || Math.abs(d.price - d.from) < tick / 2) return;
    setBusy(true);
    try {
      const r = await client.tradeProtect({ client_id: "drag" + Date.now().toString(36), account, symbol, last_price: last ?? 0, ...(d.kind === "stop" ? { sl_price: d.price } : { tp_price: d.price }) });
      onMoved?.(`${d.kind === "stop" ? "Stop" : "Objetivo"} movido a ${fmt(d.price)} arrastrando · ${r.ms} ms`);
    } catch (ex) { onMoved?.((ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "")); }
    finally { setBusy(false); }
  };
  const color: Record<Line["kind"], string> = { entry: "var(--accent)", stop: "var(--bad)", target: "var(--ok)", last: "var(--text)" };
  return (
    <div ref={box} className="chart-box candle-box" data-testid="candle-chart">
      <div className="chips small" style={{ marginBottom: 6 }}>
        {[30, 60, 120, 240].map((k) => <button key={k} className={`chip-btn ${n === k ? "active" : ""}`} onClick={() => setN(k)}>{k} min</button>)}
        <span className="muted small">{last ? `último ${fmt(last)}` : ""}{busy ? " · enviando…" : drag ? ` · soltar para mover el ${drag.kind === "stop" ? "stop" : "objetivo"} a ${fmt(drag.price)}` : (stops?.length || targets?.length) ? " · arrastra el stop o el objetivo para moverlo" : ""}</span>
      </div>
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className={`chart ${drag ? "dragging" : ""}`} onPointerMove={onMove} onPointerUp={() => void onUp()} onPointerLeave={() => { if (dragRef.current) void onUp(); }} role="img" aria-label={`Velas de 1 minuto de ${symbol}`}>
        {ticks.map((v) => <g key={v}><line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className="grid" /><text x={W - M.r + 6} y={y(v) + 4} className="tick">{fmt(v)}</text></g>)}
        {visible.map((b, i) => { const up = b.c >= b.o; return (
          <g key={b.m}>
            <line x1={x(i)} x2={x(i)} y1={y(b.h)} y2={y(b.l)} stroke={up ? "var(--ok)" : "var(--bad)"} strokeWidth={1} />
            <rect x={x(i) - bw / 2} y={y(Math.max(b.o, b.c))} width={bw} height={Math.max(1, Math.abs(y(b.o) - y(b.c)))} fill={up ? "var(--ok)" : "var(--bad)"} opacity={0.9} rx={1} />
            {i % Math.max(1, Math.ceil(visible.length / 6)) === 0 && <text x={x(i)} y={H - 6} textAnchor="middle" className="tick">{b.t}</text>}
          </g>); })}
        {lines.map((l, i) => { const p = drag && l.kind === drag.kind && (stops?.[0]?.order_id === l.orderId || targets?.[0]?.order_id === l.orderId) ? drag.price : l.price; const draggable = !disabled && !!account && (l.kind === "stop" || l.kind === "target"); return (
          <g key={l.kind + i} onPointerDown={onDown(l)} style={{ cursor: draggable ? "ns-resize" : "default" }} data-testid={`line-${l.kind}`}>
            {draggable && <rect x={M.l} y={y(p) - 8} width={iw} height={16} fill="transparent" />}
            <line x1={M.l} x2={W - M.r} y1={y(p)} y2={y(p)} stroke={color[l.kind]} strokeWidth={l.kind === "last" ? 1 : 1.5} strokeDasharray={l.kind === "last" ? "2 3" : l.kind === "entry" ? "6 3" : undefined} />
            <rect x={W - M.r + 2} y={y(p) - 8} width={M.r - 4} height={16} rx={3} fill={color[l.kind]} />
            <text x={W - M.r + 6} y={y(p) + 4} fontSize={10} fill="#0b1220" fontWeight={700}>{fmt(p)}</text>
            <text x={M.l + 4} y={y(p) - 3} fontSize={10} fill={color[l.kind]}>{l.label}</text>
          </g>); })}
      </svg>
    </div>
  );
}
