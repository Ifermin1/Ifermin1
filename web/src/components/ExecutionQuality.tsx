import { useEffect, useMemo, useRef, useState } from "react";
import type { Account, Client, Execution, Rule } from "../lib/api";
import { hhmm } from "../lib/format";

/* Calidad de ejecución: ¿entran todas las seguidoras al precio del maestro?
 * - Por operación: cada punto es una seguidora, en ticks de deslizamiento (0 = mismo precio; positivo = peor).
 * - Por seguidora: media de ticks y tiempo en bróker, la peor arriba.
 * Un solo eje, una sola tonalidad; el color de estado solo marca lo que se sale del umbral. */
const M = { l: 44, r: 12, t: 14, b: 26 };
const WARN_TICKS = 2;          // a partir de aquí una copia cuenta como "lejos del maestro"

type Row = { e: Execution; ticks: number[]; missing: number };
const fmtTicks = (t: number) => (t > 0 ? "+" : "") + (Number.isInteger(t) ? t.toFixed(0) : t.toFixed(1));
const median = (xs: number[]) => { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

export function ExecutionQuality({ client, accounts, rules, master, lastFillId, compact, onRules }:
  { client: Client; accounts: Account[]; rules: Rule[]; master: string | null; lastFillId: number | null; compact?: boolean; onRules?: (updated: Rule[]) => void }) {
  const [execs, setExecs] = useState<Execution[]>([]);
  const [view, setView] = useState<"chart" | "table">("chart");
  const [hover, setHover] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [width, setWidth] = useState(600);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const load = () => client.execution(40).then((x) => { if (alive) setExecs(x); }).catch(() => {});
    const t = window.setTimeout(load, lastFillId === null ? 0 : 400);   // el fill llega por WS; el registro se cierra un instante después
    const every = window.setInterval(load, 30000);
    return () => { alive = false; window.clearTimeout(t); window.clearInterval(every); };
  }, [client, lastFillId]);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver((e) => setWidth(Math.max(240, Math.floor(e[0].contentRect.width))));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);

  const label = (id: string) => accounts.find((a) => a.account_id.toLowerCase() === id.toLowerCase())?.alias || id;
  const rows: Row[] = useMemo(() => [...execs].reverse().map((e) => ({
    e, ticks: e.followers.filter((f) => f.slip_ticks !== null).map((f) => f.slip_ticks as number),
    missing: e.followers.filter((f) => f.filled < f.expected).length,
  })), [execs]);
  const fills = rows.flatMap((r) => r.e.followers.filter((f) => f.slip_ticks !== null));
  const same = fills.filter((f) => f.slip_ticks === 0).length;
  const meanTicks = fills.length ? fills.reduce((s, f) => s + (f.slip_ticks as number), 0) / fills.length : null;
  const brokerMed = median(fills.map((f) => f.broker_ms).filter((x): x is number => x !== null));
  const byFollower = useMemo(() => {
    const m = new Map<string, { name: string; n: number; ticks: number; same: number; broker: number[]; missing: number }>();
    for (const r of rows) for (const f of r.e.followers) {
      const k = f.name.toLowerCase();
      const s = m.get(k) ?? { name: f.name, n: 0, ticks: 0, same: 0, broker: [], missing: 0 };
      if (f.slip_ticks !== null) { s.n++; s.ticks += f.slip_ticks; if (f.slip_ticks === 0) s.same++; }
      if (f.broker_ms !== null) s.broker.push(f.broker_ms);
      if (f.filled < f.expected) s.missing++;
      m.set(k, s);
    }
    return [...m.values()].map((s) => ({ ...s, mean: s.n ? s.ticks / s.n : 0, brokerMed: median(s.broker) })).sort((a, b) => b.mean - a.mean || a.name.localeCompare(b.name));
  }, [rows]);
  const worst = byFollower[0] && byFollower[0].n ? byFollower[0] : null;

  // modo de entrada actual de las seguidoras de la maestra
  const links = rules.filter((r) => r.enabled && master && r.master_account.toLowerCase() === master.toLowerCase() && !r.symbol_filter);
  const limitLinks = links.filter((r) => r.entry_mode === "limit");
  const samePriceOn = links.length > 0 && limitLinks.length === links.length && limitLinks.every((r) => r.tolerance_ticks === 0);
  const preset = async (p: "same" | "market") => {
    const q = p === "same"
      ? `¿Entradas de ${links.length} seguidoras como LÍMITE al precio exacto del maestro (±0 ticks)?\n\nSi en 2 s no se llena, lo que falte va a mercado. Las salidas (stop/TP) no cambian.`
      : `¿Entradas de ${links.length} seguidoras A MERCADO (copia inmediata, puede haber deslizamiento)?`;
    if (!confirm(q)) return;
    setBusy(true); setMsg(null);
    try {
      const out = await client.setEntryForAll(p === "same" ? { entry_mode: "limit", tolerance_ticks: 0, entry_timeout_s: 2, entry_fallback: "market" } : { entry_mode: "market" });
      onRules?.(out);
      setMsg(`${out.length} seguidoras: entradas ${p === "same" ? "al precio del maestro (±0 ticks, 2 s, luego a mercado)" : "a mercado"}`);
    } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(false); }
  };

  // ---- gráfico por operación ----
  const shown = rows.slice(-30);
  const W = width, H = compact ? 150 : 190, iw = W - M.l - M.r, ih = H - M.t - M.b;
  let hi = 2, lo = -1;
  for (const r of shown) for (const t of r.ticks) { if (t > hi) hi = t; if (t < lo) lo = t; }
  hi = Math.ceil(hi + 0.5); lo = Math.floor(lo - 0.5);
  const x = (i: number) => M.l + ((i + 0.5) / Math.max(1, shown.length)) * iw;
  const y = (t: number) => M.t + (1 - (t - lo) / (hi - lo)) * ih;
  const yStep = hi - lo > 12 ? 4 : hi - lo > 6 ? 2 : 1;
  const yTicks: number[] = []; for (let v = Math.ceil(lo / yStep) * yStep; v <= hi; v += yStep) yTicks.push(v);
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < M.l || px > W - M.r || !shown.length) { setHover(null); return; }
    setHover(Math.min(shown.length - 1, Math.max(0, Math.floor(((px - M.l) / iw) * shown.length))));
  };
  const hov = hover !== null ? shown[hover] : null;
  const tipLeft = hover !== null && x(hover) > W * 0.45;
  // etiquetas de hora: una cada `step` operaciones y la última, sin que se pisen
  const step = Math.max(1, Math.ceil(shown.length / 6));
  const labelAt = (i: number) => i % step === 0 || (i === shown.length - 1 && (i % step) * 2 >= step);
  const barW = Math.max(1, Math.min(10, (iw / Math.max(1, shown.length)) * 0.5));
  const maxMean = Math.max(WARN_TICKS, ...byFollower.map((f) => Math.abs(f.mean)));

  return (
    <div className={`exec-quality ${compact ? "compact" : ""}`} ref={box} data-testid="exec-quality">
      <div className="exec-stats">
        <div className="exec-stat"><span>Al mismo precio</span><b className={fills.length ? (same / fills.length >= 0.8 ? "ok" : same / fills.length >= 0.5 ? "warn" : "bad") : "muted"}>{fills.length ? `${Math.round((100 * same) / fills.length)} %` : "—"}</b><i className="muted small">{fills.length ? `${same} de ${fills.length} copias` : "sin copias aún"}</i></div>
        <div className="exec-stat"><span>Deslizamiento medio</span><b className={meanTicks === null ? "muted" : meanTicks <= 0.5 ? "ok" : meanTicks <= WARN_TICKS ? "warn" : "bad"}>{meanTicks === null ? "—" : `${fmtTicks(Math.round(meanTicks * 10) / 10)} ticks`}</b><i className="muted small">positivo = peor que el maestro</i></div>
        <div className="exec-stat"><span>Tiempo en bróker</span><b className={brokerMed === null ? "muted" : brokerMed <= 300 ? "ok" : brokerMed <= 800 ? "warn" : "bad"}>{brokerMed === null ? "—" : `${Math.round(brokerMed)} ms`}</b><i className="muted small">mediana, fill maestro → fill seguidora</i></div>
        <div className="exec-stat"><span>Peor seguidora</span><b className={worst ? (worst.mean > WARN_TICKS ? "bad" : worst.mean > 0.5 ? "warn" : "ok") : "muted"}>{worst ? label(worst.name) : "—"}</b><i className="muted small">{worst ? `${fmtTicks(Math.round(worst.mean * 10) / 10)} ticks de media` : ""}</i></div>
      </div>

      <div className="chart-bar">
        <div className="chips">
          <span className="muted small">Entradas ahora: {links.length === 0 ? "sin seguidoras" : samePriceOn ? "al precio del maestro (±0)" : limitLinks.length === 0 ? "a mercado" : `${limitLinks.length} límite · ${links.length - limitLinks.length} a mercado`}</span>
          <button className={`chip-btn ${samePriceOn ? "active" : ""}`} disabled={busy || !links.length} onClick={() => void preset("same")} title="Límite al precio exacto del maestro; si en 2 s no se llena, lo que falte a mercado">Entrar al precio del maestro</button>
          <button className={`chip-btn ${links.length && limitLinks.length === 0 ? "active" : ""}`} disabled={busy || !links.length} onClick={() => void preset("market")} title="Copia inmediata a mercado">Entrar a mercado</button>
        </div>
        <button className="chip-btn" onClick={() => setView(view === "chart" ? "table" : "chart")}>{view === "chart" ? "Tabla" : "Gráfico"}</button>
      </div>
      {msg && <p className="muted small" data-testid="exec-msg">{msg}</p>}

      {rows.length === 0 ? (
        <p className="empty">Sin copias con fill todavía. Cada operación del maestro aparecerá aquí con el precio al que entró cada seguidora.</p>
      ) : view === "table" ? (
        <div className="table-wrap"><table className="chart-table exec-table">
          <thead><tr><th>Hora</th><th>Operación</th><th className="num">Maestro</th>{byFollower.map((f) => <th key={f.name} className="num">{label(f.name)}</th>)}</tr></thead>
          <tbody>{[...rows].reverse().slice(0, 25).map((r) => (
            <tr key={r.e.order_id}>
              <td className="muted">{hhmm(new Date(r.e.at))}</td>
              <td>{r.e.action} {r.e.quantity} {r.e.symbol} <span className={`tag ${r.e.kind === "exit" ? "k-stop" : "k-límite"}`}>{r.e.kind === "exit" ? "salida" : "entrada"}</span></td>
              <td className="num">{r.e.price}</td>
              {byFollower.map((c) => {
                const f = r.e.followers.find((z) => z.name.toLowerCase() === c.name.toLowerCase());
                if (!f) return <td key={c.name} className="num muted">·</td>;
                if (f.slip_ticks === null) return <td key={c.name} className="num warn" title={`${f.filled}/${f.expected} ejecutados`}>sin fill</td>;
                return <td key={c.name} className={`num ${f.slip_ticks === 0 ? "ok" : f.slip_ticks > WARN_TICKS ? "bad" : f.slip_ticks > 0 ? "warn" : "ok"}`} title={`${f.price} · ${f.broker_ms ?? f.latency_ms ?? "?"} ms`}>{fmtTicks(f.slip_ticks)}</td>;
              })}
            </tr>
          ))}</tbody>
        </table></div>
      ) : (
        <div className="exec-charts">
          <div className="chart-wrap exec-ops">
            <div className="muted small chart-title">Por operación · ticks respecto al maestro (0 = mismo precio)</div>
            <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="Deslizamiento por operación" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
              {yTicks.map((v) => (
                <g key={v}>
                  <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} className={v === 0 ? "zero" : "grid"} />
                  <text x={M.l - 8} y={y(v) + 4} textAnchor="end" className="tick">{fmtTicks(v)}</text>
                </g>
              ))}
              {shown.map((r, i) => labelAt(i) && (
                <text key={r.e.order_id} x={x(i)} y={H - 8} textAnchor="middle" className="tick">{hhmm(new Date(r.e.at))}</text>
              ))}
              {hover !== null && <rect x={x(hover) - (iw / shown.length) / 2} y={M.t} width={iw / shown.length} height={ih} className="hover-band" />}
              {shown.map((r, i) => {
                const mn = r.ticks.length ? Math.min(...r.ticks) : 0, mx = r.ticks.length ? Math.max(...r.ticks) : 0;
                return (
                  <g key={r.e.order_id}>
                    {r.ticks.length > 1 && <line x1={x(i)} x2={x(i)} y1={y(mx)} y2={y(mn)} className="range" />}
                    {r.ticks.map((t, j) => <circle key={j} cx={x(i)} cy={y(t)} r={compact ? 3 : 4} className={`dot ${t > WARN_TICKS ? "far" : ""}`} />)}
                    {r.missing > 0 && <text x={x(i)} y={M.t + 10} textAnchor="middle" className="tick warn-mark" aria-label="sin fill">!</text>}
                    {r.e.kind === "exit" && <rect x={x(i) - barW / 2} y={H - M.b + 2} width={barW} height={3} className="exit-mark" />}
                  </g>
                );
              })}
            </svg>
            {hov && (
              <div className="tooltip" style={{ [tipLeft ? "right" : "left"]: tipLeft ? W - x(hover!) + 10 : x(hover!) + 10 }}>
                <div className="muted small">{hhmm(new Date(hov.e.at))} · {hov.e.kind === "exit" ? "salida" : "entrada"} · {hov.e.action} {hov.e.quantity} {hov.e.symbol} @ <b>{hov.e.price}</b></div>
                {hov.e.followers.map((f) => (
                  <div key={f.name}><span>{label(f.name)}</span>
                    <b className={`num ${f.slip_ticks === null ? "warn" : f.slip_ticks === 0 ? "ok" : f.slip_ticks > WARN_TICKS ? "bad" : ""}`}>
                      {f.slip_ticks === null ? `sin fill (${f.filled}/${f.expected})` : `${fmtTicks(f.slip_ticks)} ticks · ${f.price}${f.broker_ms !== null ? ` · ${f.broker_ms} ms` : f.latency_ms !== null ? ` · ${f.latency_ms} ms` : ""}`}
                    </b></div>
                ))}
              </div>
            )}
            <div className="muted small legend-line"><i className="dot-sample" /> una seguidora · <i className="dot-sample far" /> más de {WARN_TICKS} ticks · <i className="exit-sample" /> salida · ! sin fill</div>
          </div>
          <div className="exec-followers">
            <div className="muted small chart-title">Por seguidora · media de ticks{compact ? "" : " (la peor arriba)"}</div>
            {byFollower.map((f) => (
              <div key={f.name} className="exec-row" title={`${f.same} de ${f.n} al mismo precio`}>
                <span className="exec-name">{label(f.name)}</span>
                <div className="meter"><i style={{ width: `${Math.min(100, (Math.abs(f.mean) / maxMean) * 100)}%` }} className={f.mean > WARN_TICKS ? "far" : f.mean <= 0.5 ? "near" : ""} /></div>
                <b className={`num ${f.mean > WARN_TICKS ? "bad" : f.mean > 0.5 ? "warn" : "ok"}`}>{fmtTicks(Math.round(f.mean * 10) / 10)}</b>
                <span className="muted small num">{f.brokerMed !== null ? `${Math.round(f.brokerMed)} ms` : ""}{f.missing ? ` · ${f.missing} sin fill` : ""}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
