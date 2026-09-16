import { useEffect, useMemo, useRef, useState } from "react";
import type { Account, Client } from "../lib/api";
import { hhmm, money, signedMoney } from "../lib/format";

/* Paleta categórica validada (modo oscuro, superficie #111a2e): orden fijo, el color sigue a la cuenta. */
const PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const MAX_SERIES = PALETTE.length;
type Pt = [number, number];           // [ms, pnl]
type Range = "1h" | "4h" | "hoy";
const RANGE_MS: Record<Range, number> = { "1h": 3600e3, "4h": 4 * 3600e3, hoy: 0 };
const M = { l: 60, r: 14, t: 12, b: 26 };

const midnight = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime(); };
const niceStep = (span: number, n = 4) => { const raw = span / n; const p = 10 ** Math.floor(Math.log10(raw)); const m = raw / p; return (m < 1.5 ? 1 : m < 3.5 ? 2.5 : m < 7.5 ? 5 : 10) * p; };
const nearest = (pts: Pt[], t: number): Pt | null => {
  if (!pts.length) return null;
  let lo = 0, hi = pts.length - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (pts[mid][0] < t) lo = mid + 1; else hi = mid; }
  const a = pts[lo], b = pts[lo - 1];
  return b && Math.abs(b[0] - t) < Math.abs(a[0] - t) ? b : a;
};

export function PnlChart({ accounts, client, master, compact }: { accounts: Account[]; client: Client; master: string | null; compact?: boolean }) {
  const [history, setHistory] = useState<Record<string, Pt[]>>({});
  const [live, setLive] = useState<Record<string, Pt[]>>({});
  const [range, setRange] = useState<Range>("hoy");
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [view, setView] = useState<"chart" | "table">("chart");
  const [hoverT, setHoverT] = useState<number | null>(null);
  const [width, setWidth] = useState(600);
  const box = useRef<HTMLDivElement>(null);
  const height = compact ? 170 : 230;

  // Histórico del engine (muestras cada 15 s) al montar y cada minuto
  useEffect(() => {
    let alive = true;
    const load = () => client.pnl(24).then((h) => {
      if (!alive) return;
      const out: Record<string, Pt[]> = {};
      for (const [acc, rows] of Object.entries(h)) out[acc] = rows.map(([ts, v]) => [new Date(ts).getTime(), v] as Pt).filter((p) => !Number.isNaN(p[0]));
      setHistory(out); setLive({});
    }).catch(() => {});
    load();
    const t = window.setInterval(load, 60000);
    return () => { alive = false; window.clearInterval(t); };
  }, [client]);

  // Puntos en vivo entre recargas: cada snapshot de cuentas (como mucho uno cada 5 s por cuenta)
  useEffect(() => {
    const now = Date.now();
    setLive((prev) => {
      let changed = false; const next = { ...prev };
      for (const a of accounts) {
        if (!a.enabled || !a.reported) continue;
        const pts = next[a.account_id] ?? [];
        const last = pts[pts.length - 1];
        if (last && now - last[0] < 5000) continue;
        next[a.account_id] = [...pts.slice(-2000), [now, a.daily_pnl]]; changed = true;
      }
      return changed ? next : prev;
    });
  }, [accounts]);

  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver((e) => setWidth(Math.max(240, Math.floor(e[0].contentRect.width))));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  const label = (id: string) => accounts.find((a) => a.account_id === id)?.alias || id;
  const since = range === "hoy" ? midnight() : Date.now() - RANGE_MS[range];
  const now = Date.now();

  // Series: maestra primero y el resto por nombre; el índice (y su color) no depende de filtros ni de qué se oculte
  const all = useMemo(() => {
    const ids = new Set([...Object.keys(history), ...Object.keys(live)]);
    return [...ids].sort((x, y) => (x === master ? -1 : y === master ? 1 : x.localeCompare(y)));
  }, [history, live, master]);
  const series = useMemo(() => all.slice(0, MAX_SERIES).map((id, i) => {
    const h = history[id] ?? []; const lastH = h.length ? h[h.length - 1][0] : 0;
    const pts = [...h, ...(live[id] ?? []).filter((p) => p[0] > lastH)].filter((p) => p[0] >= since);
    return { id, color: PALETTE[i], pts, last: pts.length ? pts[pts.length - 1][1] : null };
  }), [all, history, live, since]);
  const visible = series.filter((s) => !hidden.has(s.id) && s.pts.length);
  const extra = all.length - MAX_SERIES;
  // "hoy" arranca en la primera muestra del día (redondeada a 15 min), no a medianoche: si no, la sesión queda aplastada a la derecha
  const firstPt = Math.min(...visible.map((s) => s.pts[0][0]));
  const x0 = range === "hoy" && visible.length ? Math.max(since, Math.floor(firstPt / (15 * 60e3)) * 15 * 60e3) : since;

  let lo = 0, hi = 0;
  for (const s of visible) for (const [, v] of s.pts) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (hi - lo < 1) { lo -= 100; hi += 100; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const W = width, H = height, iw = W - M.l - M.r, ih = H - M.t - M.b;
  const x = (t: number) => M.l + ((t - x0) / Math.max(1, now - x0)) * iw;
  const y = (v: number) => M.t + (1 - (v - lo) / (hi - lo)) * ih;
  const yStep = niceStep(hi - lo);
  const yTicks: number[] = []; for (let v = Math.ceil(lo / yStep) * yStep; v <= hi; v += yStep) yTicks.push(v);
  const span = now - x0;
  const xStep = span <= 90 * 60e3 ? 15 * 60e3 : span <= 5 * 3600e3 ? 30 * 60e3 : span <= 9 * 3600e3 ? 3600e3 : 2 * 3600e3;
  const xTicks: number[] = []; for (let t = Math.ceil(x0 / xStep) * xStep; t <= now; t += xStep) xTicks.push(t);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < M.l || px > W - M.r) { setHoverT(null); return; }
    setHoverT(x0 + ((px - M.l) / iw) * (now - x0));
  };
  const hover = hoverT === null ? null : visible.map((s) => ({ s, p: nearest(s.pts, hoverT) })).filter((h) => h.p);
  const hoverX = hover && hover.length ? x(hover[0].p![0]) : null;
  const tipLeft = hoverX !== null && hoverX > W * 0.6;

  // Tabla: una fila cada 5 min con el último valor conocido de cada serie
  const tableRows = useMemo(() => {
    if (view !== "table") return [];
    const step = 5 * 60e3; const rows: { t: number; vals: (number | null)[] }[] = [];
    for (let t = Math.floor(now / step) * step; t >= since && rows.length < 36; t -= step) {
      rows.push({ t, vals: visible.map((s) => { const p = [...s.pts].reverse().find((q) => q[0] <= t); return p ? p[1] : null; }) });
    }
    return rows;
  }, [view, visible, since, now]);

  return (
    <div className={`pnl-chart ${compact ? "compact" : ""}`} ref={box}>
      <div className="chart-bar">
        <div className="chips">
          {(["1h", "4h", "hoy"] as Range[]).map((r) => <button key={r} className={`chip-btn ${range === r ? "active" : ""}`} onClick={() => setRange(r)}>{r}</button>)}
        </div>
        <button className="chip-btn" onClick={() => setView(view === "chart" ? "table" : "chart")}>{view === "chart" ? "Tabla" : "Gráfico"}</button>
      </div>
      {series.length >= 1 && (
        <ul className="legend">
          {series.map((s) => (
            <li key={s.id} className={hidden.has(s.id) ? "off" : ""}>
              <button onClick={() => setHidden((h) => { const n = new Set(h); n.has(s.id) ? n.delete(s.id) : n.add(s.id); return n; })} title={hidden.has(s.id) ? "Mostrar" : "Ocultar"}>
                <i className="swatch" style={{ background: s.color }} /><span>{label(s.id)}</span>
                {s.last !== null && <b className={`num ${s.last > 0 ? "ok" : s.last < 0 ? "bad" : "muted"}`}>{signedMoney(s.last)}</b>}
              </button>
            </li>
          ))}
          {extra > 0 && <li className="muted small">y {extra} más (usa la tabla)</li>}
        </ul>
      )}
      {visible.length === 0 ? (
        <p className="empty">Sin muestras {range === "hoy" ? "hoy" : `en la última ${range}`}: el engine guarda el P&L de las cuentas activas cada 15 s.</p>
      ) : view === "table" ? (
        <div className="table-wrap"><table className="chart-table">
          <thead><tr><th>Hora</th>{visible.map((s) => <th key={s.id} className="num"><i className="swatch" style={{ background: s.color }} />{label(s.id)}</th>)}</tr></thead>
          <tbody>{tableRows.map((r) => <tr key={r.t}><td className="muted">{hhmm(new Date(r.t))}</td>{r.vals.map((v, i) => <td key={i} className="num">{v === null ? "—" : money(v)}</td>)}</tr>)}</tbody>
        </table></div>
      ) : (
        <div className="chart-wrap">
          <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="P&L del día por cuenta"
               onPointerMove={onMove} onPointerLeave={() => setHoverT(null)}>
            {yTicks.map((v) => (
              <g key={v}>
                <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className={v === 0 ? "zero" : "grid"} />
                <text x={M.l - 8} y={y(v) + 4} textAnchor="end" className="tick">{v === 0 ? "0" : Math.abs(v) >= 1000 ? `${v / 1000}k` : v}</text>
              </g>
            ))}
            {xTicks.map((t) => <text key={t} x={x(t)} y={H - 8} textAnchor="middle" className="tick">{hhmm(new Date(t))}</text>)}
            {visible.map((s) => (
              <g key={s.id}>
                <path d={s.pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ")} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                <circle cx={x(s.pts[s.pts.length - 1][0])} cy={y(s.pts[s.pts.length - 1][1])} r={4} fill={s.color} className="end-marker" />
              </g>
            ))}
            {hoverX !== null && <line x1={hoverX} x2={hoverX} y1={M.t} y2={H - M.b} className="crosshair" />}
            {hover?.map(({ s, p }) => <circle key={s.id} cx={x(p![0])} cy={y(p![1])} r={4} fill={s.color} className="end-marker" />)}
          </svg>
          {hover && hover.length > 0 && hoverX !== null && (
            <div className="tooltip" style={{ [tipLeft ? "right" : "left"]: tipLeft ? W - hoverX + 10 : hoverX + 10 }}>
              <div className="muted small">{hhmm(new Date(hover[0].p![0]))}</div>
              {hover.map(({ s, p }) => <div key={s.id}><i className="swatch" style={{ background: s.color }} />{label(s.id)} <b className="num">{signedMoney(p![1])}</b></div>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
