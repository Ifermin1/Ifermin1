import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../lib/store";
import type { AnalysisData, AnalysisDay, AnalysisParams, CashEvent, CashEventInput, Trade } from "../lib/api";
import { Card, Empty, Kpi } from "../components/ui";
import { Icon } from "../components/Icons";
import { EquityCurve, HistogramChart, SignedBars } from "../components/charts";
import { byAccount, byHour, bySide, bySymbol, byWeekday, dayStats, histogram, tradeStats, type TradeLike } from "../lib/stats";

/* Análisis de operaciones: duración vs. resultado, gestión del riesgo por operación, operaciones una por una, retiros y
 * pagos, balance con conciliación diaria y los criterios de cálculo. Todo sale de las operaciones reconstruidas por el
 * engine (fills de maestra y seguidoras), del P&L y saldo que reporta NinjaTrader y de los movimientos de caja anotados. */

type Tab = "stats" | "duration" | "records" | "balance" | "criteria";
type Range = "30" | "90" | "ytd" | "all";
type Mode = "gross" | "net";
type Kind = "gain" | "loss" | "be" | "none";
type Row = { t: Trade; gross: number; fee: number; net: number; value: number; dur: number | null; kind: Kind; short: boolean };

const TABS: { id: Tab; label: string }[] = [
  { id: "stats", label: "Estadísticas" }, { id: "duration", label: "Duración y riesgo" }, { id: "records", label: "Pendientes y registros" },
  { id: "balance", label: "Balance y retiros" }, { id: "criteria", label: "Datos y criterios" },
];
const DEFAULT_PARAMS: AnalysisParams = { commission_rt: 5, be_tolerance: 0, loss_reference: 250, short_seconds: 60, start_balance: null, use_engine_commissions: true };
const KIND_LABEL: Record<Kind, string> = { gain: "Ganancia", loss: "Pérdida", be: "Breakeven", none: "Sin PNL" };
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const usd = (n: number, decimals?: number) => {
  const a = Math.abs(n);
  const dec = decimals ?? (a >= 100 || a === 0 ? 0 : 2);
  return `${n < 0 ? "−" : ""}USD ${a.toLocaleString("es-ES", { minimumFractionDigits: dec, maximumFractionDigits: dec })}`;
};
const dollars = (n: number) => `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString("es-ES", { maximumFractionDigits: Math.abs(n) >= 100 ? 0 : 2 })}`;
const pct = (x: number | null, d = 2) => (x === null ? "—" : `${(100 * x).toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d })} %`);
const hms = (isoStr: string | null) => (isoStr ? new Date(isoStr).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }) : "—");
const dayShort = (day: string) => new Date(day + "T12:00:00").toLocaleDateString("es-ES", { day: "2-digit", month: "short" }).replace(".", "");
const dayLong = (day: string) => new Date(day + "T12:00:00").toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "long" });
const fmtDur = (s: number | null) => {
  if (s === null) return "—";
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} m ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} m`;
};
const seconds = (t: Trade): number | null => (t.opened_at ? Math.max(0, Math.round((new Date(t.closed_at).getTime() - new Date(t.opened_at).getTime()) / 1000)) : null);
const rangeFrom = (r: Range): string => {
  const d = new Date();
  if (r === "30") d.setDate(d.getDate() - 30);
  else if (r === "90") d.setDate(d.getDate() - 90);
  else if (r === "ytd") { d.setMonth(0, 1); }
  else return "2000-01-01";
  return iso(d);
};
const download = (name: string, text: string) => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  a.download = name; a.click();
  window.setTimeout(() => URL.revokeObjectURL(a.href), 2000);
};

export function Analysis() {
  const { client, accounts, health, audit } = useStore();
  const [tab, setTab] = useState<Tab>(() => { const m = window.location.hash.match(/analysis\/(\w+)/); return (TABS.find((t) => t.id === m?.[1])?.id ?? "stats"); });
  const [range, setRange] = useState<Range>("90");
  const [account, setAccount] = useState("");
  const [mode, setMode] = useState<Mode>("gross");
  const [data, setData] = useState<AnalysisData | null>(null);
  const [params, setParams] = useState<AnalysisParams>(DEFAULT_PARAMS);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const lastFillId = audit.find((a) => a.event_type === "FOLLOWER_FILL" || a.event_type === "ACCOUNT_FILL" || a.event_type === "MASTER_RECEIVED")?.id ?? null;
  const from = rangeFrom(range);
  const to = useMemo(() => { const d = new Date(); d.setDate(d.getDate() + 1); return iso(d); }, []);

  useEffect(() => { if (client) client.analysisParams().then(setParams).catch(() => {}); }, [client]);
  useEffect(() => {
    if (!client) return;
    let alive = true;
    const t = window.setTimeout(() => client.analysis(from, to, account).then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : String(e)); }), lastFillId === null ? 0 : 800);
    return () => { alive = false; window.clearTimeout(t); };
  }, [client, from, to, account, lastFillId, tick]);
  const reload = () => setTick((n) => n + 1);
  const go = (t: Tab) => { setTab(t); window.location.hash = `analysis/${t}`; };

  const label = (id: string) => accounts.find((a) => a.account_id.toLowerCase() === id.toLowerCase())?.alias || id;
  const master = health?.bridge.master_account ?? null;
  const accountOptions = Array.from(new Set([...accounts.filter((a) => a.enabled).map((a) => a.account_id), ...(data?.accounts ?? [])]));

  // ---- una fila por operación con comisión, neto, duración y clasificación ----
  const rows: Row[] = useMemo(() => (data?.trades ?? []).map((t) => {
    const gross = Number(t.pnl ?? 0);
    const fee = params.use_engine_commissions ? Number(t.commissions ?? 0) : t.quantity * params.commission_rt;
    const net = Math.round((gross - fee) * 100) / 100;
    const value = mode === "gross" ? gross : net;
    const dur = seconds(t);
    const kind: Kind = t.pnl === null || t.pnl === undefined ? "none" : Math.abs(value) <= params.be_tolerance ? "be" : value > 0 ? "gain" : "loss";
    return { t, gross, fee, net, value, dur, kind, short: dur !== null && dur < params.short_seconds };
  }), [data, params, mode]);

  const risk = useMemo(() => {
    const losses = rows.filter((r) => r.kind === "loss");
    const within = losses.filter((r) => Math.abs(r.value) <= params.loss_reference);
    const worst = losses.reduce((m, r) => Math.min(m, r.value), 0);
    const gross = rows.reduce((s, r) => s + r.gross, 0), fee = rows.reduce((s, r) => s + r.fee, 0);
    const posAll = rows.filter((r) => r.gross > 0).reduce((s, r) => s + r.gross, 0);
    const posShort = rows.filter((r) => r.gross > 0 && r.short).reduce((s, r) => s + r.gross, 0);
    const shortBE = rows.filter((r) => r.short && r.gross >= r.fee && Math.abs(r.net) <= params.be_tolerance).length;
    const shortN = rows.filter((r) => r.short).length;
    const shortWins = rows.filter((r) => r.short && r.kind === "gain").length;
    return { losses: losses.length, within: within.length, above: losses.length - within.length, worst, gross, fee, net: gross - fee,
             contrib: posAll > 0 ? posShort / posAll : null, shortBE, shortN, shortWins, known: rows.filter((r) => r.kind !== "none").length };
  }, [rows, params]);

  const days = data?.days ?? [];
  const cash = data?.cash ?? [];

  if (!client) return null;
  return (
    <div className="grid" data-testid="analysis">
      <Card>
        <div className="perf-bar">
          <div className="an-tabs" role="tablist" data-testid="an-tabs">
            {TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "active" : ""} onClick={() => go(t.id)}>{t.label}</button>)}
          </div>
          <div className="chips">
            <div className="view-tabs" data-testid="an-range">
              {(["30", "90", "ytd", "all"] as Range[]).map((r) => <button key={r} className={range === r ? "active" : ""} onClick={() => setRange(r)}>{{ "30": "30 d", "90": "90 d", ytd: "Año", all: "Todo" }[r]}</button>)}
            </div>
            <label className="inline">Cuenta
              <select value={account} onChange={(e) => setAccount(e.target.value)} data-testid="an-account">
                <option value="">Todas</option>
                {accountOptions.map((id) => <option key={id} value={id}>{label(id)}{id === master ? " · maestra" : ""}</option>)}
              </select>
            </label>
            {(tab === "duration" || tab === "stats") && (
              <div className="view-tabs" data-testid="an-mode">
                <button className={mode === "gross" ? "active" : ""} onClick={() => setMode("gross")}>Bruto</button>
                <button className={mode === "net" ? "active" : ""} onClick={() => setMode("net")}>Neto</button>
              </div>
            )}
          </div>
        </div>
      </Card>
      {error && <div className="banner bad">No se pudo cargar el análisis: {error}</div>}
      {tab === "stats" && <StatsTab rows={rows} days={days} params={params} mode={mode} label={label} />}
      {tab === "duration" && <DurationTab rows={rows} risk={risk} params={params} mode={mode} label={label} />}
      {tab === "records" && <RecordsTab cash={cash} accounts={accountOptions} account={account} label={label} onChange={reload} />}
      {tab === "balance" && <BalanceTab days={days} rows={rows} cash={cash} params={params} account={account} label={label} />}
      {tab === "criteria" && <CriteriaTab params={params} onSave={async (p) => { const saved = await client.saveAnalysisParams(p); setParams(saved); }}
                                          data={data} rows={rows} account={account} label={label} />}
    </div>
  );
}

/* ================================ Duración y riesgo ================================ */
type RiskStats = { losses: number; within: number; above: number; worst: number; gross: number; fee: number; net: number; contrib: number | null; shortBE: number; shortN: number; shortWins: number; known: number };
function DurationTab({ rows, risk, params, mode, label }: { rows: Row[]; risk: RiskStats; params: AnalysisParams; mode: Mode; label: (id: string) => string }) {
  const [selected, setSelected] = useState<number | null>(null);
  const sel = rows.find((r) => r.t.id === selected) ?? null;
  const modeLabel = mode === "gross" ? "bruto" : "neto";
  return (
    <>
      <div className="an-two">
        <Card title="Tiempo vs. resultado" icon="clock" right={<span className="muted small">Cada punto es una operación. Selecciónalo para ver su detalle.</span>}>
          <Scatter rows={rows} params={params} mode={mode} selected={selected} onSelect={setSelected} />
          <div className="an-legend">
            <span className="muted small">Eje de tiempo logarítmico (segundos).</span>
            <span><i className="dot gain" />Ganancia</span><span><i className="dot loss" />Pérdida</span><span><i className="dot be" />Breakeven</span><span><i className="dot none" />Sin PNL</span>
          </div>
          {sel && <TradeDetail r={sel} label={label} onClose={() => setSelected(null)} />}
        </Card>
        <Card title="Gestión del riesgo por operación" icon="shield" right={<span className="muted small">Referencia personal: {usd(params.loss_reference)} de pérdida. No es una regla del prop firm.</span>}>
          <div className="risk-boxes">
            <div className="risk-box" data-testid="risk-within">
              <span className="muted">Pérdidas dentro de tu referencia</span>
              <b className="big">{risk.losses ? pct(risk.within / risk.losses) : "—"}</b>
              <span className="muted small">{risk.within} de {risk.losses} pérdidas ≤ {dollars(params.loss_reference)} en valor absoluto.</span>
            </div>
            <div className="risk-box" data-testid="risk-above">
              <span className="muted">Por encima de tu referencia</span>
              <b className={`big ${risk.above ? "bad" : "ok"}`}>{risk.above}</b>
              <span className="muted small">Mayor pérdida: {risk.worst < 0 ? `${usd(risk.worst)} · ${modeLabel}` : "ninguna"}.</span>
            </div>
          </div>
          <div className="an-rows">
            <div><span>PNL bruto de todas las operaciones</span><b>{usd(risk.gross)}</b></div>
            <div><span>Comisiones estimadas</span><b>{usd(risk.fee)}</b></div>
            <div><span>PNL neto de todas las operaciones</span><b className={risk.net > 0 ? "ok" : risk.net < 0 ? "bad" : ""}>{usd(risk.net)}</b></div>
            <div><span>Aporte &lt; {params.short_seconds} s a ganancias brutas positivas</span><b>{pct(risk.contrib)}</b></div>
            <div><span>Aciertos en operaciones &lt; {params.short_seconds} s</span><b>{risk.shortN ? `${risk.shortWins} de ${risk.shortN} · ${pct(risk.shortWins / risk.shortN, 0)}` : "—"}</b></div>
            <div><span>Trades &lt; {params.short_seconds} s que cubren comisión y quedan en BE</span><b>{risk.shortBE}</b></div>
          </div>
          <p className="muted small an-note">{risk.known} de {rows.length} operaciones tienen PNL conocido. Las sumas excluyen importes faltantes. Aporte = ganancias positivas de trades cortos / ganancias positivas de todos los trades. BE {modeLabel}: ±{usd(params.be_tolerance)}. El historial no identifica si la salida fue por stop-loss.</p>
        </Card>
      </div>
      <TradesTable rows={rows} params={params} label={label} onSelect={(id) => { setSelected(id); document.querySelector('[data-testid="scatter"]')?.scrollIntoView({ behavior: "smooth", block: "center" }); }} />
    </>
  );
}

const SM = { l: 56, r: 16, t: 20, b: 40 };
const X_TICKS = [0, 5, 15, 30, 60, 120, 300, 900, 1800, 3600, 14400];
function Scatter({ rows, params, mode, selected, onSelect }: { rows: Row[]; params: AnalysisParams; mode: Mode; selected: number | null; onSelect: (id: number | null) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<Row | null>(null);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver((e) => setWidth(Math.max(280, Math.floor(e[0].contentRect.width))));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  const pts = rows.filter((r) => r.dur !== null);
  const W = width, H = 320, iw = W - SM.l - SM.r, ih = H - SM.t - SM.b;
  const maxS = Math.max(params.short_seconds * 2, ...pts.map((r) => r.dur as number));
  const lx = (s: number) => Math.log10(1 + s);
  const x = (s: number) => SM.l + (lx(s) / lx(maxS * 1.15)) * iw;
  let lo = Math.min(-params.loss_reference * 1.2, ...pts.map((r) => r.value)), hi = Math.max(100, ...pts.map((r) => r.value));
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const y = (v: number) => SM.t + (1 - (v - lo) / (hi - lo)) * ih;
  const yTicks = useMemo(() => { const span = hi - lo; const raw = span / 5; const p = 10 ** Math.floor(Math.log10(raw)); const m = raw / p; const st = (m < 1.5 ? 1 : m < 3.5 ? 2.5 : m < 7.5 ? 5 : 10) * p; const t: number[] = []; for (let v = Math.ceil(lo / st) * st; v <= hi; v += st) t.push(Math.round(v)); return t; }, [lo, hi]);
  const xTicks = X_TICKS.filter((s) => s <= maxS * 1.15);
  const tipX = hover ? x(hover.dur as number) : 0;
  return (
    <div ref={box} className="scatter-box" data-testid="scatter">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="scatter" role="img" aria-label="Duración de cada operación frente a su resultado">
        <rect x={SM.l} y={SM.t} width={Math.max(0, x(params.short_seconds) - SM.l)} height={ih} className="short-zone" />
        {yTicks.map((v) => <g key={v}><line x1={SM.l} x2={W - SM.r} y1={y(v)} y2={y(v)} className={v === 0 ? "zero" : "grid"} /><text x={SM.l - 8} y={y(v) + 4} textAnchor="end" className="tick">{v.toLocaleString("es-ES")}</text></g>)}
        {xTicks.map((s) => <g key={s}><line x1={x(s)} x2={x(s)} y1={H - SM.b} y2={H - SM.b + 4} className="grid" /><text x={x(s)} y={H - SM.b + 16} textAnchor="middle" className={`tick ${s === params.short_seconds ? "warn" : ""}`}>{s}</text></g>)}
        <text x={SM.l} y={SM.t - 8} className="tick">USD · {mode === "gross" ? "bruto" : "neto"}</text>
        <text x={W - SM.r} y={H - 6} textAnchor="end" className="tick">Duración (s)</text>
        <line x1={x(params.short_seconds)} x2={x(params.short_seconds)} y1={SM.t} y2={H - SM.b} className="ref warn" />
        <text x={x(params.short_seconds) + 4} y={SM.t - 8} className="tick warn">{params.short_seconds} s</text>
        {params.loss_reference > 0 && <>
          <line x1={SM.l} x2={W - SM.r} y1={y(-params.loss_reference)} y2={y(-params.loss_reference)} className="ref bad" />
          <text x={W - SM.r} y={y(-params.loss_reference) - 4} textAnchor="end" className="tick bad">−{params.loss_reference} USD</text>
        </>}
        {pts.map((r) => (
          <circle key={r.t.id} cx={x(r.dur as number)} cy={y(r.value)} r={selected === r.t.id ? 7 : 4.5} className={`pt ${r.kind} ${selected === r.t.id ? "sel" : ""}`}
                  data-testid={`pt-${r.t.id}`} tabIndex={0} role="button" aria-label={`Operación ${r.t.id}: ${fmtDur(r.dur)}, ${usd(r.value)}`}
                  onPointerEnter={() => setHover(r)} onPointerLeave={() => setHover(null)} onFocus={() => setHover(r)} onBlur={() => setHover(null)}
                  onClick={() => onSelect(selected === r.t.id ? null : r.t.id)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(r.t.id); } }} />
        ))}
      </svg>
      {pts.length === 0 && <Empty>No hay operaciones con hora de entrada en este periodo.</Empty>}
      {hover && (
        <div className="scatter-tip" style={{ left: Math.min(W - 190, Math.max(0, tipX - 90)), top: Math.max(0, y(hover.value) - 78) }} data-testid="scatter-tip">
          <b>T{hover.t.id} · {hover.t.symbol}</b>
          <span>{hover.t.side === "long" ? "Largo" : "Corto"} × {hover.t.quantity} · {fmtDur(hover.dur)}</span>
          <span className={hover.kind === "gain" ? "ok" : hover.kind === "loss" ? "bad" : ""}>{KIND_LABEL[hover.kind]} · {usd(hover.value)}</span>
        </div>
      )}
    </div>
  );
}

function TradeDetail({ r, label, onClose }: { r: Row; label: (id: string) => string; onClose: () => void }) {
  const t = r.t;
  return (
    <div className="trade-detail" data-testid="trade-detail">
      <div className="card-head"><h3>Operación T{t.id} · {t.symbol} · {label(t.account_id)}</h3><button className="chip-btn" onClick={onClose}>Cerrar</button></div>
      <div className="an-rows two">
        <div><span>Jornada</span><b>{dayLong(t.day)}</b></div>
        <div><span>Dirección · contratos</span><b>{t.side === "long" ? "Largo" : "Corto"} × {t.quantity}</b></div>
        <div><span>Entrada → salida</span><b>{hms(t.opened_at)} → {hms(t.closed_at)}</b></div>
        <div><span>Duración</span><b>{fmtDur(r.dur)}{r.short ? " · corta" : ""}</b></div>
        <div><span>Precio medio entrada → salida</span><b>{t.entry_price} → {t.exit_price}</b></div>
        <div><span>Ejecuciones (fills)</span><b>{t.fills}</b></div>
        <div><span>PNL bruto · comisión</span><b>{usd(r.gross, 2)} · {usd(r.fee, 2)}</b></div>
        <div><span>PNL neto</span><b className={r.net > 0 ? "ok" : r.net < 0 ? "bad" : ""}>{usd(r.net, 2)}</b></div>
      </div>
    </div>
  );
}

function TradesTable({ rows, params, label, onSelect }: { rows: Row[]; params: AnalysisParams; label: (id: string) => string; onSelect: (id: number) => void }) {
  const [fDur, setFDur] = useState<"all" | "short" | "long">("all");
  const [fRes, setFRes] = useState<"all" | Kind>("all");
  const [sort, setSort] = useState<"recent" | "oldest" | "best" | "worst" | "longest">("recent");
  const [limit, setLimit] = useState(50);
  const list = useMemo(() => {
    const f = rows.filter((r) => (fDur === "all" || (fDur === "short" ? r.short : r.dur !== null && !r.short)) && (fRes === "all" || r.kind === fRes));
    const key = (r: Row) => new Date(r.t.closed_at).getTime();
    return f.sort((a, b) => sort === "recent" ? key(b) - key(a) : sort === "oldest" ? key(a) - key(b) : sort === "best" ? b.value - a.value : sort === "worst" ? a.value - b.value : (b.dur ?? -1) - (a.dur ?? -1));
  }, [rows, fDur, fRes, sort]);
  const exportCsv = () => {
    const head = ["operacion", "cuenta", "jornada", "simbolo", "direccion", "contratos", "entrada", "salida", "duracion_s", "pnl_bruto", "comision", "pnl_neto", "clasificacion"];
    const lines = list.map((r) => [r.t.id, r.t.account_id, r.t.day, r.t.symbol, r.t.side, r.t.quantity, r.t.opened_at ?? "", r.t.closed_at, r.dur ?? "", r.gross.toFixed(2), r.fee.toFixed(2), r.net.toFixed(2), KIND_LABEL[r.kind]]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`).join(","));
    download(`operaciones_${iso(new Date())}.csv`, [head.join(","), ...lines].join("\n"));
  };
  return (
    <Card title="Operaciones, una por una" icon="list" right={
      <div className="chips an-filters">
        <select value={fDur} onChange={(e) => setFDur(e.target.value as typeof fDur)} data-testid="f-dur"><option value="all">Todas las duraciones</option><option value="short">Menos de {params.short_seconds} s</option><option value="long">{params.short_seconds} s o más</option></select>
        <select value={fRes} onChange={(e) => setFRes(e.target.value as typeof fRes)} data-testid="f-res"><option value="all">Todos los resultados</option><option value="gain">Ganancias</option><option value="loss">Pérdidas</option><option value="be">Breakeven</option></select>
        <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} data-testid="f-sort"><option value="recent">Más recientes</option><option value="oldest">Más antiguas</option><option value="best">Mayor resultado</option><option value="worst">Menor resultado</option><option value="longest">Más largas</option></select>
        <button className="small-btn" onClick={exportCsv} disabled={!list.length} data-testid="export-csv"><Icon name="download" size={14} /> Exportar CSV</button>
      </div>}>
      <p className="muted small">Duración total de la posición · horarios locales · comisiones: {params.use_engine_commissions ? "las anotadas por el engine" : `USD ${params.commission_rt} por contrato completo`}. {list.length} operaciones.</p>
      {list.length === 0 ? <Empty>No hay operaciones con esos filtros.</Empty> : (
        <div className="table-wrap">
          <table className="trades an-table" data-testid="trades-table">
            <thead><tr><th>Operación</th><th>Jornada</th><th>Dirección</th><th className="num">Contratos</th><th>Entrada → salida</th><th className="num">Duración</th><th className="num">PNL bruto</th><th className="num">Comisión</th><th className="num">PNL neto</th><th>Clasificación</th></tr></thead>
            <tbody>
              {list.slice(0, limit).map((r) => (
                <tr key={r.t.id} onClick={() => onSelect(r.t.id)} className="clickable">
                  <td><b className="accent">T{r.t.id}</b><br /><span className="muted small">{r.t.symbol} · {label(r.t.account_id)}</span></td>
                  <td>{dayShort(r.t.day)}</td>
                  <td><span className="tag">{r.t.side === "long" ? "Long" : "Short"}</span></td>
                  <td className="num">{r.t.quantity}</td>
                  <td>{hms(r.t.opened_at)} → {hms(r.t.closed_at)}</td>
                  <td className="num"><span className={`tag ${r.short ? "k-short" : ""}`}>{fmtDur(r.dur)}</span></td>
                  <td className={`num ${r.gross > 0 ? "ok" : r.gross < 0 ? "bad" : ""}`}>{dollars(r.gross)}</td>
                  <td className="num">{dollars(r.fee)}</td>
                  <td className={`num ${r.net > 0 ? "ok" : r.net < 0 ? "bad" : ""}`}>{dollars(r.net)}</td>
                  <td className={r.kind === "gain" ? "ok" : r.kind === "loss" ? "bad" : "muted"}>{KIND_LABEL[r.kind]}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {list.length > limit && <button className="chip-btn" onClick={() => setLimit(limit + 100)}>Mostrar más ({list.length - limit} restantes)</button>}
        </div>
      )}
    </Card>
  );
}

/* ================================ Pendientes y registros ================================ */
const KIND_ES = { withdrawal: "Retiro", deposit: "Depósito", adjust: "Ajuste" } as const;
const STATUS_ES = { pending: "Pendiente", completed: "Completado", rejected: "Rechazado" } as const;
function RecordsTab({ cash, accounts, account, label, onChange }: { cash: CashEvent[]; accounts: string[]; account: string; label: (id: string) => string; onChange: () => void }) {
  const { client, accounts: live } = useStore();
  const [form, setForm] = useState<CashEventInput>({ account_id: account || accounts[0] || "", day: iso(new Date()), kind: "withdrawal", amount: 0, paid: null, status: "pending", note: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { if (account) setForm((f) => ({ ...f, account_id: account })); }, [account]);
  const pending = cash.filter((c) => c.status === "pending");
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    if (!client) return;
    setBusy(true);
    try { await fn(); setMsg(ok); onChange(); } catch (e) { setMsg(`Error: ${e instanceof Error ? e.message : String(e)}`); } finally { setBusy(false); }
  };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.account_id || !form.amount) { setMsg("Indica cuenta e importe."); return; }
    void run(() => client!.addCash(form), "Movimiento registrado.").then(() => setForm((f) => ({ ...f, amount: 0, paid: null, note: "" })));
  };
  const markPaid = (c: CashEvent) => {
    const v = prompt(`Importe recibido por el retiro de ${usd(c.amount)} (tras el reparto del prop firm):`, String(c.paid ?? c.amount));
    if (v === null) return;
    const paid = Number(v.replace(",", "."));
    if (!Number.isFinite(paid)) { setMsg("Importe no válido."); return; }
    void run(() => client!.updateCash(c.id, { status: "completed", paid }), "Retiro marcado como pagado.");
  };
  const open = live.filter((a) => a.enabled && (!account || a.account_id === account)).flatMap((a) => a.open_positions.map((p) => ({ ...p, account: a.account_id })));
  return (
    <>
      <div className="an-two">
        <Card title="Retiros pendientes" icon="wallet" right={<span className="badge accent">{pending.length}</span>}>
          {pending.length === 0 ? <Empty>No hay solicitudes pendientes.</Empty> : (
            <ul className="an-list" data-testid="pending-list">
              {pending.map((c) => (
                <li key={c.id}>
                  <div><b>{usd(c.amount)}</b> · {KIND_ES[c.kind]} · {label(c.account_id)}<br /><span className="muted small">{dayShort(c.day)}{c.note ? ` · ${c.note}` : ""}</span></div>
                  <div className="chips">
                    <button className="chip-btn" disabled={busy} onClick={() => markPaid(c)} data-testid={`paid-${c.id}`}>Marcar pagado</button>
                    <button className="chip-btn danger" disabled={busy} onClick={() => void run(() => client!.updateCash(c.id, { status: "rejected" }), "Solicitud marcada como rechazada.")}>Rechazado</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <h3 className="an-h3">Posiciones en curso</h3>
          {open.length === 0 ? <Empty>Sin posiciones abiertas ahora mismo.</Empty> : (
            <ul className="an-list">
              {open.map((p) => <li key={p.account + p.symbol}><div><b>{p.symbol}</b> · {p.quantity > 0 ? "Largo" : "Corto"} × {Math.abs(p.quantity)} @ {p.avg_price}<br /><span className="muted small">{label(p.account)}</span></div><b className={p.unrealized_pnl > 0 ? "ok" : p.unrealized_pnl < 0 ? "bad" : ""}>{dollars(p.unrealized_pnl)}</b></li>)}
            </ul>
          )}
          <p className="muted small">Las posiciones abiertas se convierten en operaciones del historial cuando se cierran.</p>
        </Card>
        <Card title="Registrar movimiento" icon="download">
          <form className="cash-form" onSubmit={submit} data-testid="cash-form">
            <label>Cuenta<select value={form.account_id} onChange={(e) => setForm({ ...form, account_id: e.target.value })}>{accounts.map((id) => <option key={id} value={id}>{label(id)}</option>)}</select></label>
            <label>Fecha<input type="date" value={form.day} onChange={(e) => setForm({ ...form, day: e.target.value })} required /></label>
            <label>Tipo<select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as CashEvent["kind"] })}><option value="withdrawal">Retiro (se descuenta del saldo)</option><option value="deposit">Depósito / reset (se suma)</option><option value="adjust">Ajuste (no cambia el saldo)</option></select></label>
            <label>Importe (USD)<input type="number" min="0" step="0.01" value={form.amount || ""} onChange={(e) => setForm({ ...form, amount: Number(e.target.value) || 0 })} required data-testid="cash-amount" /></label>
            <label>Recibido (USD)<input type="number" min="0" step="0.01" value={form.paid ?? ""} placeholder="tras el reparto" onChange={(e) => setForm({ ...form, paid: e.target.value === "" ? null : Number(e.target.value) })} /></label>
            <label>Estado<select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as CashEvent["status"] })}><option value="pending">Pendiente</option><option value="completed">Completado</option><option value="rejected">Rechazado</option></select></label>
            <label className="wide">Nota<input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="p. ej. solicitud nº 3 · Rise" /></label>
            <div className="chips wide"><button className="primary" type="submit" disabled={busy} data-testid="cash-save">Guardar movimiento</button>{msg && <span className={`small ${msg.startsWith("Error") ? "bad" : "ok"}`} data-testid="cash-msg">{msg}</span>}</div>
          </form>
          <p className="muted small">Los retiros restan del saldo esperado en "Balance y retiros"; el importe recibido es lo que llegó a tu cuenta bancaria tras el reparto del prop firm.</p>
        </Card>
      </div>
      <Card title="Registro de movimientos" icon="list">
        {cash.length === 0 ? <Empty>No hay movimientos en este periodo.</Empty> : (
          <div className="table-wrap">
            <table className="trades an-table" data-testid="cash-table">
              <thead><tr><th>Fecha</th><th>Cuenta</th><th>Tipo</th><th className="num">Importe</th><th className="num">Recibido</th><th>Estado</th><th>Nota</th><th /></tr></thead>
              <tbody>
                {[...cash].reverse().map((c) => (
                  <tr key={c.id}>
                    <td>{dayShort(c.day)}</td><td>{label(c.account_id)}</td><td>{KIND_ES[c.kind]}</td>
                    <td className="num">{usd(c.amount)}</td><td className="num">{c.paid === null ? "—" : usd(c.paid)}</td>
                    <td><span className={`badge ${c.status === "completed" ? "ok" : c.status === "pending" ? "warn" : "bad"}`}>{STATUS_ES[c.status]}</span></td>
                    <td className="muted">{c.note}</td>
                    <td className="num"><button className="link tiny" disabled={busy} onClick={() => { if (confirm(`¿Borrar el movimiento de ${usd(c.amount)} del ${dayShort(c.day)}?`)) void run(() => client!.deleteCash(c.id), "Movimiento borrado."); }} data-testid={`cash-del-${c.id}`}>Borrar</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

/* ================================ Balance y retiros ================================ */
const BM = { l: 60, r: 16, t: 16, b: 32 };
type DayRow = AnalysisDay & { fee: number; net: number; cumNet: number; expected: number | null; check: "ok" | "missing" | "nobroker" | "balance" | "balance_ok" | "none" };
function BalanceTab({ days, rows, cash, params, account, label }: { days: AnalysisDay[]; rows: Row[]; cash: CashEvent[]; params: AnalysisParams; account: string; label: (id: string) => string }) {
  const today = iso(new Date());
  const table: DayRow[] = useMemo(() => {
    const feeByDay = new Map<string, number>();
    for (const r of rows) feeByDay.set(r.t.day, (feeByDay.get(r.t.day) ?? 0) + r.fee);
    let cum = 0;
    let prevBalance: number | null = null;
    return days.filter((d) => d.trades || d.pnl_broker !== null || d.balance !== null || d.withdrawals || d.deposits).map((d) => {
      const fee = params.use_engine_commissions ? d.commissions : (feeByDay.get(d.day) ?? 0);
      const gross = d.trades ? d.pnl_trades : (d.pnl_broker ?? 0);
      const net = Math.round((gross - fee) * 100) / 100;
      cum = Math.round((cum + net) * 100) / 100;
      const expected = prevBalance === null ? null : Math.round((prevBalance + net - d.withdrawals + d.deposits) * 100) / 100;
      let check: DayRow["check"] = "none";
      if (d.pnl_broker !== null && d.trades) check = Math.abs(d.pnl_broker - d.pnl_trades) <= Math.max(5, Math.abs(d.pnl_broker) * 0.01) ? "ok" : "balance";
      else if (d.pnl_broker !== null && !d.trades && Math.abs(d.pnl_broker) > 0.5) check = "missing";
      else if (d.trades && d.pnl_broker === null) check = "nobroker";
      if (check === "balance" && d.balance !== null && expected !== null && Math.abs(d.balance - expected) <= 5) check = "ok";
      if (check === "none" && d.balance !== null && expected !== null && Math.abs(d.balance - expected) <= 5) check = "balance_ok";
      if (d.balance !== null) prevBalance = d.balance;
      return { ...d, fee, net, cumNet: cum, expected, check };
    });
  }, [days, rows, params]);
  const withBalance = table.filter((d) => d.balance !== null);
  const last = withBalance.length ? withBalance[withBalance.length - 1] : null;
  const first = withBalance[0] ?? null;
  const gross = table.reduce((s, d) => s + (d.trades ? d.pnl_trades : (d.pnl_broker ?? 0)), 0);
  const fee = table.reduce((s, d) => s + d.fee, 0);
  const net = gross - fee;
  const withdrawals = cash.filter((c) => c.kind === "withdrawal" && c.status !== "rejected");
  const wSum = withdrawals.reduce((s, c) => s + c.amount, 0);
  const paid = withdrawals.filter((c) => c.status === "completed").reduce((s, c) => s + (c.paid ?? 0), 0);
  const deposits = cash.filter((c) => c.kind === "deposit" && c.status !== "rejected").reduce((s, c) => s + c.amount, 0);
  // saldo inicial: el parámetro, o el primer saldo de cierre deshaciendo el resultado y los retiros de ese día
  const base = (account ? params.start_balance : null) ?? (first ? Math.round(((first.balance as number) - first.net + first.withdrawals - first.deposits) * 100) / 100 : null);
  const okDays = table.filter((d) => d.check === "ok").length, checkable = table.filter((d) => d.check !== "none").length;
  const expectedNow = base !== null ? Math.round((base + net - wSum + deposits) * 100) / 100 : null;
  const diff = last && expectedNow !== null ? Math.round(((last.balance as number) - expectedNow) * 100) / 100 : null;
  const todayRow = table.find((d) => d.day === today);
  const totals = { trades: table.reduce((s, d) => s + d.trades, 0), gross, fee, net, w: table.reduce((s, d) => s + d.withdrawals, 0) };
  const CHECK: Record<DayRow["check"], { label: string; cls: string }> = { ok: { label: "Coincide", cls: "ok" }, balance_ok: { label: "Saldo conciliado", cls: "ok" }, missing: { label: "Falta historial", cls: "warn" }, nobroker: { label: "Sin P&L del bróker", cls: "muted" }, balance: { label: "Revisar", cls: "bad" }, none: { label: "—", cls: "muted" } };
  return (
    <>
      <p className="muted small an-sub">{account ? label(account) : "Todas las cuentas"} · {last ? `último saldo reportado el ${dayLong(last.day)}` : "sin saldo reportado todavía"} · {table.length} jornadas en el periodo.</p>
      <div className="kpis an-kpis" data-testid="balance-kpis">
        <Kpi label="Último saldo reportado" icon="wallet" value={last ? dollars(last.balance as number) : "—"} tone="accent" sub={last ? `${dayShort(last.day)} · incluye retiros` : "NinjaTrader aún no ha reportado saldo"} />
        <Kpi label="PNL neto acumulado" icon="trendUp" value={dollars(net)} tone={net > 0 ? "ok" : net < 0 ? "bad" : "muted"} sub={`${dollars(gross)} bruto − ${dollars(fee)} comisiones`} />
        <Kpi label="Retiros descontados" icon="download" value={dollars(wSum)} tone="muted" sub={`${withdrawals.filter((c) => c.status === "completed").length} completados · ${withdrawals.filter((c) => c.status === "pending").length} pendientes`} />
        <Kpi label="Importes pagados" icon="check" value={dollars(paid)} tone={paid > 0 ? "ok" : "muted"} sub="importes recibidos tras el reparto" />
      </div>
      <div className="an-two wide-left">
        <Card title="Saldo y resultado acumulado" icon="activity" right={<span className="muted small">{todayRow ? "La jornada de hoy es un corte intradía. " : ""}La línea de saldo refleja los retiros.</span>}>
          <BalanceChart rows={table} base={base} />
          <div className="an-legend"><span><i className="line solid" />Saldo reportado</span><span><i className="line dashed" />{base !== null ? `USD ${base.toLocaleString("es-ES", { maximumFractionDigits: 0 })} + PNL neto, sin retiros` : "saldo inicial + PNL neto"}</span></div>
        </Card>
        <Card title="Conciliación del reporte" icon="scale">
          <p className="muted small">P&L diario de NinjaTrader, saldo reportado e historial de operaciones.</p>
          <div className="an-rows" data-testid="reconcile">
            <div><span>Jornadas con bruto del bróker e historial conciliados</span><b>{okDays} / {checkable}</b></div>
            <div><span>Saldo inicial {account && params.start_balance !== null ? "(parámetro)" : "(deducido del primer saldo)"}</span><b>{base !== null ? dollars(base) : "—"}</b></div>
            {todayRow && <div><span>Neto de la sesión {dayShort(todayRow.day)}</span><b className={todayRow.net > 0 ? "ok" : todayRow.net < 0 ? "bad" : ""}>{dollars(todayRow.net)}</b></div>}
            <div><span>Saldo esperado (inicial + neto − retiros + depósitos)</span><b>{expectedNow !== null ? dollars(expectedNow) : "—"}</b></div>
            <div><span>Saldo general reportado</span><b>{last ? dollars(last.balance as number) : "—"}</b></div>
            <div><span>Diferencia</span><b className={diff === null ? "muted" : Math.abs(diff) <= 1 ? "ok" : "warn"}>{diff === null ? "—" : Math.abs(diff) <= 1 ? "conciliado" : dollars(diff)}</b></div>
          </div>
          <p className="muted small an-note">Una diferencia suele ser una comisión distinta a la estimada, un retiro sin anotar o una jornada sin historial (operaciones hechas con el engine apagado). Anota los retiros en "Pendientes y registros" y ajusta las comisiones en "Datos y criterios".</p>
        </Card>
      </div>
      <Card title="Actividad diaria" icon="calendar" right={<span className="muted small">Comisiones: {params.use_engine_commissions ? "las anotadas por el engine" : `USD ${params.commission_rt} por contrato completo`}.</span>}>
        {table.length === 0 ? <Empty>Sin actividad en este periodo.</Empty> : (
          <div className="table-wrap">
            <table className="trades an-table" data-testid="daily-table">
              <thead><tr><th>Jornada</th><th className="num">Trades con horario</th><th className="num">PNL bruto</th><th className="num">Comisiones</th><th className="num">PNL neto</th><th className="num">Retiro</th><th className="num">Saldo</th><th>Verificación</th></tr></thead>
              <tbody>
                {table.map((d) => (
                  <tr key={d.day}>
                    <td>{dayShort(d.day)}{d.day === today ? " · en curso" : ""}</td>
                    <td className="num">{d.trades || "—"}</td>
                    <td className="num">{dollars(d.trades ? d.pnl_trades : (d.pnl_broker ?? 0))}</td>
                    <td className="num warn">{dollars(d.fee)}</td>
                    <td className={`num ${d.net > 0 ? "ok" : d.net < 0 ? "bad" : ""}`}>{dollars(d.net)}</td>
                    <td className="num">{d.withdrawals ? dollars(d.withdrawals) : "$0"}</td>
                    <td className="num">{d.balance !== null ? dollars(d.balance) : "—"}</td>
                    <td><span className={`badge ${CHECK[d.check].cls}`} title={d.pnl_broker !== null ? `bróker ${dollars(d.pnl_broker)} · historial ${dollars(d.pnl_trades)}` : "sin P&L del bróker ese día"}>{d.day === today && d.check === "ok" ? "Saldo conciliado" : CHECK[d.check].label}</span></td>
                  </tr>
                ))}
                <tr className="total"><td><b>Total</b></td><td className="num"><b>{totals.trades}</b></td><td className="num"><b>{dollars(totals.gross)}</b></td><td className="num"><b>{dollars(totals.fee)}</b></td><td className="num"><b>{dollars(totals.net)}</b></td><td className="num"><b>{dollars(totals.w)}</b></td><td className="num"><b>{last ? dollars(last.balance as number) : "—"}</b></td><td /></tr>
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function BalanceChart({ rows, base }: { rows: DayRow[]; base: number | null }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    if (!box.current) return;
    const ro = new ResizeObserver((e) => setWidth(Math.max(280, Math.floor(e[0].contentRect.width))));
    ro.observe(box.current);
    return () => ro.disconnect();
  }, []);
  const W = width, H = 300, iw = W - BM.l - BM.r, ih = H - BM.t - BM.b;
  const pts = rows.map((d, i) => ({ i, day: d.day, balance: d.balance, ref: base !== null ? base + d.cumNet : null, w: d.withdrawals }));
  const vals = pts.flatMap((p) => [p.balance, p.ref]).filter((v): v is number => v !== null);
  if (!vals.length) return <Empty>Sin saldo reportado en este periodo.</Empty>;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (hi - lo < 500) { lo -= 250; hi += 250; }
  const pad = (hi - lo) * 0.1; lo -= pad; hi += pad;
  const x = (i: number) => BM.l + ((i + 0.5) / Math.max(1, pts.length)) * iw;
  const y = (v: number) => BM.t + (1 - (v - lo) / (hi - lo)) * ih;
  const ticks: number[] = []; { const raw = (hi - lo) / 5; const p = 10 ** Math.floor(Math.log10(raw)); const m = raw / p; const st = (m < 1.5 ? 1 : m < 3.5 ? 2.5 : m < 7.5 ? 5 : 10) * p; for (let v = Math.ceil(lo / st) * st; v <= hi; v += st) ticks.push(v); }
  const path = (key: "balance" | "ref") => pts.filter((p) => p[key] !== null).map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p[key] as number).toFixed(1)}`).join(" ");
  const step = Math.max(1, Math.ceil(pts.length / Math.max(3, Math.floor(iw / 70))));
  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    if (px < BM.l || px > W - BM.r) { setHover(null); return; }
    setHover(Math.min(pts.length - 1, Math.max(0, Math.floor(((px - BM.l) / iw) * pts.length))));
  };
  const h = hover !== null ? pts[hover] : null;
  return (
    <div ref={box} className="scatter-box" data-testid="balance-chart">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="balance-chart" role="img" aria-label="Saldo reportado y saldo inicial más resultado neto" onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {ticks.map((v) => <g key={v}><line x1={BM.l} x2={W - BM.r} y1={y(v)} y2={y(v)} className="grid" /><text x={BM.l - 8} y={y(v) + 4} textAnchor="end" className="tick">{v.toLocaleString("es-ES", { maximumFractionDigits: 0 })}</text></g>)}
        {pts.map((p) => (p.i % step === 0 || p.i === pts.length - 1) && <text key={p.day} x={x(p.i)} y={H - 8} textAnchor="middle" className="tick">{dayShort(p.day)}</text>)}
        {base !== null && <path d={path("ref")} className="ref-line" />}
        <path d={path("balance")} className="bal-line" />
        {pts.filter((p) => p.ref !== null).map((p) => <circle key={"r" + p.day} cx={x(p.i)} cy={y(p.ref as number)} r={3} className="ref-dot" />)}
        {pts.filter((p) => p.balance !== null).map((p) => <circle key={p.day} cx={x(p.i)} cy={y(p.balance as number)} r={hover === p.i ? 5.5 : 4} className="bal-dot" />)}
        {pts.filter((p) => p.w > 0 && p.balance !== null).map((p) => <text key={"w" + p.day} x={x(p.i)} y={y(p.balance as number) + 18} textAnchor="middle" className="tick warn">−{p.w.toLocaleString("es-ES")}</text>)}
        {h && <line x1={x(h.i)} x2={x(h.i)} y1={BM.t} y2={H - BM.b} className="cross" />}
      </svg>
      {h && (
        <div className="scatter-tip" style={{ left: Math.min(W - 200, Math.max(0, x(h.i) - 95)), top: 4 }}>
          <b>{dayLong(h.day)}</b>
          <span>Saldo reportado: {h.balance !== null ? dollars(h.balance) : "—"}</span>
          <span>Inicial + neto: {h.ref !== null ? dollars(h.ref) : "—"}</span>
          {h.w > 0 && <span className="warn">Retiro: {dollars(h.w)}</span>}
        </div>
      )}
    </div>
  );
}

/* ================================ Datos y criterios ================================ */
function CriteriaTab({ params, onSave, data, rows, account, label }: { params: AnalysisParams; onSave: (p: AnalysisParams) => Promise<void>; data: AnalysisData | null; rows: Row[]; account: string; label: (id: string) => string }) {
  const [form, setForm] = useState<AnalysisParams>(params);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setForm(params); }, [params]);
  const num = (k: keyof AnalysisParams) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value === "" && k === "start_balance" ? null : Number(e.target.value) || 0 });
  const save = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true);
    try { await onSave(form); setMsg("Parámetros aplicados."); } catch (err) { setMsg(`Error: ${err instanceof Error ? err.message : String(err)}`); } finally { setBusy(false); }
  };
  const noEntry = rows.filter((r) => r.dur === null).length;
  return (
    <>
      <div className="an-two">
        <Card title="Parámetros de tu gestión" icon="gauge">
          <p className="muted small">Se guardan en el engine y valen para todos tus dispositivos. No cambian los datos originales.</p>
          <form className="cash-form" onSubmit={save} data-testid="params-form">
            <label>Comisión / contrato completo (USD)<input type="number" min="0" step="0.01" value={form.commission_rt} onChange={num("commission_rt")} data-testid="p-commission" /><span className="muted small">Ida y vuelta, por contrato. Solo se usa si no descuentas las comisiones anotadas por el engine.</span></label>
            <label>Tolerancia breakeven (± USD)<input type="number" min="0" step="0.01" value={form.be_tolerance} onChange={num("be_tolerance")} data-testid="p-be" /><span className="muted small">Con 0, solo un resultado exactamente nulo es BE.</span></label>
            <label>Referencia de pérdida (USD)<input type="number" min="0" step="1" value={form.loss_reference} onChange={num("loss_reference")} data-testid="p-loss" /><span className="muted small">Tu referencia por operación. Valor inicial: 250.</span></label>
            <label>Operación corta: menos de (s)<input type="number" min="1" max="3600" step="1" value={form.short_seconds} onChange={num("short_seconds")} data-testid="p-short" /><span className="muted small">Límite para "trade corto". Valor inicial: 60.</span></label>
            <label>Saldo inicial de la cuenta (USD)<input type="number" min="0" step="1" value={form.start_balance ?? ""} placeholder="se deduce del primer saldo" onChange={num("start_balance")} data-testid="p-start" /><span className="muted small">Vacío = se deduce del primer saldo reportado del periodo. Solo se aplica con una cuenta elegida.</span></label>
            <label className="inline"><input type="checkbox" checked={form.use_engine_commissions} onChange={(e) => setForm({ ...form, use_engine_commissions: e.target.checked })} data-testid="p-engine-fees" /> Usar las comisiones anotadas por el engine (Riesgo → Comisiones)</label>
            <div className="chips wide"><button className="primary" type="submit" disabled={busy} data-testid="params-save">Aplicar parámetros</button>{msg && <span className={`small ${msg.startsWith("Error") ? "bad" : "ok"}`} data-testid="params-msg">{msg}</span>}</div>
          </form>
        </Card>
        <Card title="Estado del historial" icon="list">
          <p className="muted small">{account ? label(account) : "Todas las cuentas"} · operaciones reconstruidas por el engine a partir de las ejecuciones.</p>
          <div className="an-rows" data-testid="history-state">
            <div><span>Ejecuciones leídas</span><b>{data?.fills ?? 0}</b></div>
            <div><span>Posiciones reconstruidas</span><b>{rows.length}</b></div>
            <div><span>Sin hora de entrada (abiertas antes de arrancar el engine)</span><b className={noEntry ? "warn" : "ok"}>{noEntry}</b></div>
            <div><span>Jornadas con datos</span><b>{data?.days.length ?? 0}</b></div>
            <div><span>Periodo</span><b>{data ? `${dayShort(data.from)} → ${dayShort(data.to)}` : "—"}</b></div>
            <div><span>Cuentas en el periodo</span><b>{(data?.accounts ?? []).map(label).join(", ") || "—"}</b></div>
            <div><span>Tipo de orden de salida</span><b className="muted">No se registra</b></div>
          </div>
        </Card>
      </div>
      <Card title="Cómo se calcula cada indicador" icon="target">
        <div className="an-defs">
          <h4>Una operación = una posición completa</h4>
          <p>El engine sigue la cantidad comprada y vendida de cada cuenta por instrumento hasta volver a cero. Las entradas y salidas parciales pertenecen a una misma posición; un giro cierra una y abre otra. Se guardan el precio medio de entrada y de salida, el P&L en dólares (puntos × valor del punto) y las ejecuciones que la formaron. Una posición que ya estaba abierta al arrancar el engine se cierra como operación, pero sin hora de entrada: no participa en las métricas de duración.</p>
          <h4>Duración y límite de {params.short_seconds} segundos</h4>
          <p>Tiempo desde la primera entrada hasta el último cierre. Solo se cuenta como corta una duración estrictamente menor a {params.short_seconds} s. Los horarios tienen precisión de segundos y se muestran en la hora local de este dispositivo.</p>
          <h4>Clasificación</h4>
          <p>Ganancia, pérdida o breakeven según el modo elegido (bruto o neto). Es BE cuando el resultado está dentro de ±{usd(params.be_tolerance)}. Un cambio entre bruto y neto no altera el número de operaciones cortas.</p>
          <h4>Ganancias, PNL y comisiones</h4>
          <p>PNL bruto = suma del P&L de las operaciones. Comisión = la anotada por el engine con cada ejecución (Riesgo → Comisiones) o, si lo desactivas, contratos × USD {params.commission_rt} por contrato completo. PNL neto = bruto − comisión. El aporte de trades cortos a las ganancias brutas positivas excluye las pérdidas del denominador.</p>
          <h4>Referencia de pérdida</h4>
          <p>"Dentro de tu referencia" cuenta las pérdidas cuyo valor absoluto no supera USD {params.loss_reference}. Es una referencia personal para revisar tu gestión, no una regla del prop firm.</p>
          <h4>Balance y conciliación</h4>
          <p>El saldo reportado es el que NinjaTrader informa para la cuenta al cierre de cada jornada (se guarda el último valor del día). La línea "inicial + PNL neto" parte del saldo inicial (parámetro o deducido del primer saldo) y suma el neto de cada jornada sin descontar retiros: la distancia entre las dos líneas son los retiros anotados. Una jornada "Coincide" cuando el P&L diario del bróker y la suma del historial difieren menos de un 1 % (o 1 USD); "Falta historial" cuando el bróker reporta P&L de un día sin operaciones guardadas (engine apagado); "Revisar" cuando no cuadran.</p>
        </div>
      </Card>
    </>
  );
}


/* ================================ Estadísticas ================================ */
const num = (x: number | null, d = 2, suffix = "") => (x === null ? "—" : x === Infinity ? "∞" : `${x.toLocaleString("es-ES", { minimumFractionDigits: d, maximumFractionDigits: d })}${suffix}`);
function StatsTab({ rows, days, params, mode, label }: { rows: Row[]; days: AnalysisDay[]; params: AnalysisParams; mode: Mode; label: (id: string) => string }) {
  const trades: TradeLike[] = useMemo(() => rows.map((r) => ({ ...r.t, commissions: r.fee })), [rows]);
  const value = (t: TradeLike) => (mode === "gross" ? t.pnl : Math.round((t.pnl - t.commissions) * 100) / 100);
  const ts = useMemo(() => tradeStats(trades, value, params.be_tolerance), [trades, mode, params.be_tolerance]);   // eslint-disable-line react-hooks/exhaustive-deps
  const dayNet = useMemo(() => {
    const feeByDay = new Map<string, number>();
    for (const r of rows) feeByDay.set(r.t.day, (feeByDay.get(r.t.day) ?? 0) + r.fee);
    return days.filter((d) => d.trades || d.pnl_broker !== null).map((d) => {
      const gross = d.trades ? d.pnl_trades : (d.pnl_broker ?? 0);
      const fee = mode === "gross" ? 0 : params.use_engine_commissions ? d.commissions : (feeByDay.get(d.day) ?? 0);
      return { day: d.day, net: Math.round((gross - fee) * 100) / 100 };
    });
  }, [days, rows, params, mode]);
  const ds = useMemo(() => dayStats(dayNet), [dayNet]);
  const modeLabel = mode === "gross" ? "bruto" : "neto";
  const tone = (x: number) => (x > 0 ? "ok" : x < 0 ? "bad" : "muted");
  return (
    <>
      <div className="kpis an-kpis stats-kpis" data-testid="stats-kpis">
        <Kpi label={`Resultado ${modeLabel}`} icon="wallet" value={dollars(mode === "gross" ? ts.gross : ts.net)} tone={tone(mode === "gross" ? ts.gross : ts.net)} sub={`${ts.n} operaciones · ${ds.n} jornadas`} />
        <Kpi label="Aciertos" icon="target" value={ts.winRate === null ? "—" : `${Math.round(ts.winRate * 100)} %`} ring={ts.winRate === null ? null : ts.winRate * 100} tone={ts.winRate === null ? "muted" : ts.winRate >= 0.5 ? "ok" : "warn"} sub={`${ts.wins} ganadoras · ${ts.losses} perdedoras · ${ts.be} BE`} />
        <Kpi label="Factor de beneficio" icon="scale" value={num(ts.pf)} tone={ts.pf === null ? "muted" : ts.pf >= 1.5 ? "ok" : ts.pf >= 1 ? "warn" : "bad"} sub={`ganado ${dollars(ts.grossProfit)} ÷ perdido ${dollars(ts.grossLoss)}`} />
        <Kpi label="Esperanza por operación" icon="trendUp" value={ts.expectancy === null ? "—" : dollars(ts.expectancy)} tone={ts.expectancy === null ? "muted" : tone(ts.expectancy)} sub={ts.rExpectancy !== null ? `${num(ts.rExpectancy)} R · Kelly ${num(ts.kelly !== null ? ts.kelly * 100 : null, 0, " %")}` : "neto medio por operación"} />
        <Kpi label="Media gan. / perd." icon="layers" value={<>{ts.avgWin === null ? "—" : <span className="ok">{dollars(ts.avgWin)}</span>} <span className="muted">/</span> {ts.avgLoss === null ? "—" : <span className="bad">{dollars(-ts.avgLoss)}</span>}</>} bar={[ts.avgWin ?? 0, ts.avgLoss ?? 0]} sub={ts.payoff !== null ? `ratio ${num(ts.payoff)} · mayor ${dollars(ts.largestWin)} / ${dollars(ts.largestLoss)}` : "por operación"} />
        <Kpi label="Drawdown máximo" icon="thumbDown" value={ds.maxDd ? dollars(-ds.maxDd) : "—"} tone={ds.maxDd ? (ds.maxDdPct !== null && ds.maxDdPct > 0.5 ? "bad" : "warn") : "muted"} sub={ds.maxDdPct !== null ? `${Math.round(ds.maxDdPct * 100)} % del máximo acumulado (${dollars(ds.peak)})` : "sobre la curva acumulada"} />
        <Kpi label="Sharpe · Sortino" icon="gauge" value={<>{num(ds.sharpe, 1)} <span className="muted">·</span> {num(ds.sortino, 1)}</>} tone={ds.sharpe === null ? "muted" : ds.sharpe >= 1.5 ? "ok" : ds.sharpe >= 0.5 ? "warn" : "bad"} sub={`anualizados · Calmar ${num(ds.calmar, 1)}`} />
        <Kpi label="Rachas" icon="flame" value={<>{ts.maxWinStreak} <span className="muted">/</span> {ts.maxLossStreak}</>} tone={ts.currentStreak > 0 ? "ok" : ts.currentStreak < 0 ? "bad" : "muted"} sub={`máx. ganadoras / perdedoras · ahora ${ts.currentStreak > 0 ? `+${ts.currentStreak}` : ts.currentStreak}${ds.streak ? ` · ${Math.abs(ds.streak)} días ${ds.streak > 0 ? "verdes" : "rojos"}` : ""}`} />
      </div>
      <div className="an-two wide-left">
        <Card title="Curva de capital" icon="trendUp" right={<span className="muted small">acumulado {modeLabel} por jornada · sombreado = drawdown desde el máximo</span>}>
          <EquityCurve points={ds.equity} testId="equity-chart" />
          <div className="an-rows two">
            <div><span>Mejor jornada</span><b className="ok">{ds.best ? `${dollars(ds.best.net)} · ${dayShort(ds.best.day)}` : "—"}</b></div>
            <div><span>Peor jornada</span><b className="bad">{ds.worst && ds.worst.net < 0 ? `${dollars(ds.worst.net)} · ${dayShort(ds.worst.day)}` : "—"}</b></div>
            <div><span>Jornadas verdes / rojas</span><b>{ds.green} / {ds.red}</b></div>
            <div><span>Media por jornada · desviación</span><b>{ds.avg === null ? "—" : dollars(ds.avg)} · {ds.dailyStd === null ? "—" : dollars(ds.dailyStd)}</b></div>
          </div>
        </Card>
        <Card title="Distribución del resultado" icon="activity" right={<span className="muted small">operaciones por tramo de {modeLabel}</span>}>
          <HistogramChart bins={histogram(trades, value)} testId="hist-chart" />
          <div className="an-rows two">
            <div><span>Desviación típica por operación</span><b>{ts.stdDev === null ? "—" : dollars(ts.stdDev)}</b></div>
            <div><span>Duración media ganadoras / perdedoras</span><b>{fmtDur(ts.avgDurWin === null ? null : Math.round(ts.avgDurWin))} / {fmtDur(ts.avgDurLoss === null ? null : Math.round(ts.avgDurLoss))}</b></div>
          </div>
        </Card>
      </div>
      <div className="an-two">
        <Card title="Por hora de entrada" icon="clock" right={<span className="muted small">hora local · {modeLabel}</span>}>
          <SignedBars data={byHour(trades, value)} testId="hour-chart" />
        </Card>
        <Card title="Por día de la semana" icon="calendar" right={<span className="muted small">{modeLabel}</span>}>
          <SignedBars data={byWeekday(trades, value)} testId="weekday-chart" />
        </Card>
      </div>
      <div className="an-two">
        <Card title="Por símbolo y dirección" icon="layers">
          <SignedBars data={[...bySymbol(trades, value), ...bySide(trades, value)]} height={160} testId="symbol-chart" />
          <BucketTable data={[...bySymbol(trades, value), ...bySide(trades, value)]} />
        </Card>
        <Card title="Por cuenta" icon="users">
          <SignedBars data={byAccount(trades, value).map((b) => ({ ...b, label: label(b.key) }))} height={160} testId="account-chart" />
          <BucketTable data={byAccount(trades, value).map((b) => ({ ...b, label: label(b.key) }))} />
        </Card>
      </div>
      <p className="muted small">Definiciones: factor de beneficio = suma de ganancias ÷ suma de pérdidas. Esperanza = resultado medio por operación; en R, dividido por la pérdida media. Kelly = aciertos − (1 − aciertos) ÷ ratio ganancia/pérdida (fracción teórica de riesgo; úsalo como referencia, no como regla). Sharpe y Sortino = media diaria ÷ desviación (total o solo de los días negativos) × √252. Calmar = resultado anualizado ÷ drawdown máximo. Drawdown máximo = mayor caída de la curva acumulada desde su máximo en el periodo.</p>
    </>
  );
}
function BucketTable({ data }: { data: { key: string; label: string; n: number; net: number; winRate: number | null }[] }) {
  if (!data.length) return null;
  return (
    <div className="table-wrap"><table className="trades an-table bucket-table">
      <thead><tr><th>Grupo</th><th className="num">Operaciones</th><th className="num">Aciertos</th><th className="num">Resultado</th><th className="num">Media</th></tr></thead>
      <tbody>{data.map((b) => <tr key={b.key}><td>{b.label}</td><td className="num">{b.n}</td><td className="num">{b.winRate === null ? "—" : `${Math.round(b.winRate * 100)} %`}</td><td className={`num ${b.net > 0 ? "ok" : b.net < 0 ? "bad" : ""}`}>{dollars(b.net)}</td><td className="num">{b.n ? dollars(b.net / b.n) : "—"}</td></tr>)}</tbody>
    </table></div>
  );
}
