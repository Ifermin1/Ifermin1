import { useEffect, useMemo, useState } from "react";
import type { Client, NewsAlerts, NewsAnalysis, NewsEvent, NewsFeed, NewsReaction, NewsStats as NewsStatsT } from "../lib/api";
import { Icon } from "./Icons";

/* Calendario económico: la semana en siete columnas, con impacto (rojo alto, naranja medio, ámbar bajo), hora local,
 * previsión y dato anterior; filtros de país e impacto; el próximo evento de alto impacto con cuenta atrás; el detalle de
 * cada evento con el análisis de la IA (antes: impacto posible; después: resultado) y la reacción del precio; y métricas. */
const IMPACT_LABEL: Record<string, string> = { high: "alto", medium: "medio", low: "bajo", holiday: "festivo" };
const hm = (iso: string) => new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", hour12: false });
const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export const untilText = (ms: number) => { const m = Math.round(ms / 60000); return m < 1 ? "ahora" : m < 60 ? `en ${m} min` : m < 1440 ? `en ${Math.floor(m / 60)} h ${m % 60} min` : `en ${Math.round(m / 1440)} días`; };
const pct = (a: number, b: number) => `${((b - a) / a * 100 >= 0 ? "+" : "")}${((b - a) / a * 100).toFixed(2)} %`;
const BIAS_TONE: Record<string, string> = { alcista: "ok", bajista: "bad", volátil: "warn", neutral: "muted" };

export function useNews(client: Client | null, days = 7, countries = "USD", minImpact = "low", everyMs = 60000, nonce = 0) {
  const [feed, setFeed] = useState<NewsFeed | null>(null);
  useEffect(() => {
    if (!client) return;
    let alive = true;
    const load = () => client.news(days, countries, minImpact).then((f) => { if (alive) setFeed(f); }).catch(() => {});
    load();
    const t = window.setInterval(load, everyMs);
    return () => { alive = false; window.clearInterval(t); };
  }, [client, days, countries, minImpact, everyMs, nonce]);
  return feed;
}

/** Avisos de noticias para Inicio: a punto de salir (con análisis previo), la siguiente y los últimos datos publicados. Cada minuto. */
export function useNewsAlerts(client: Client | null, everyMs = 60000) {
  const [alerts, setAlerts] = useState<NewsAlerts | null>(null);
  useEffect(() => {
    if (!client) return;
    let alive = true;
    const load = () => client.newsAlerts().then((a) => { if (alive) setAlerts(a); }).catch(() => {});
    load();
    const t = window.setInterval(load, everyMs);
    return () => { alive = false; window.clearInterval(t); };
  }, [client, everyMs]);
  return alerts;
}

/** Texto corto de un análisis para un aviso: resumen + movimiento esperado + consejo. */
export function analysisLine(a: NewsAnalysis | null | undefined): string {
  if (!a) return "";
  const d = a.data;
  return [d.summary, d.move ? `Movimiento esperado: ${d.move}.` : "", d.advice].filter(Boolean).join(" ");
}

export function AnalysisBox({ a, kind, onRedo, busy, aiOn }: { a: NewsAnalysis | null | undefined; kind: "pre" | "post"; onRedo?: () => void; busy?: boolean; aiOn?: boolean }) {
  const d = a?.data;
  return (
    <div className={`news-analysis ${kind}`} data-testid={`news-analysis-${kind}`}>
      <div className="news-analysis-head">
        <b>{kind === "pre" ? "Impacto posible" : "Resultado y efecto en el mercado"}</b>
        {a && <span className={`badge ${a.source === "ia" ? "accent" : ""}`} title={a.source === "ia" ? `Claude · ${a.model}` : "Análisis por reglas (sin ANTHROPIC_API_KEY)"}>{a.source === "ia" ? "IA" : "reglas"}</span>}
        {d?.bias && <span className={`badge ${BIAS_TONE[d.bias] ?? ""}`}>{d.bias}</span>}
        {kind === "post" && d?.surprise && <span className="badge">{d.surprise === "mayor" ? "por encima" : d.surprise === "menor" ? "por debajo" : d.surprise}</span>}
        {onRedo && <button className="link tiny" onClick={onRedo} disabled={busy}>{busy ? "analizando…" : a ? "volver a analizar" : aiOn ? "analizar con IA" : "analizar"}</button>}
      </div>
      {!a ? <p className="muted small">{busy ? "Pidiendo el análisis…" : "Sin análisis todavía."}</p> : (
        <>
          <p>{d?.summary}</p>
          {d?.move && <p className="small"><span className="muted">Movimiento esperado:</span> {d.move}</p>}
          {d?.advice && <p className="small news-advice"><Icon name="shield" size={13} /> {d.advice}</p>}
        </>
      )}
    </div>
  );
}

export function ReactionBox({ r }: { r: NewsReaction }) {
  return (
    <div className="news-reaction" data-testid="news-reaction">
      <b>{r.symbol}</b><span className="muted small">antes {r.px_before}</span>
      {([["5 min", r.px_5m], ["15 min", r.px_15m], ["30 min", r.px_30m]] as const).map(([k, v]) => (
        <span key={k} className={v === null ? "muted" : v >= r.px_before ? "ok" : "bad"}>{k}: {v === null ? "—" : `${v} (${pct(r.px_before, v)})`}</span>
      ))}
      {r.high !== null && r.low !== null && <span className="muted small">rango {r.low}–{r.high} ({((r.high - r.low) / r.px_before * 100).toFixed(2)} %)</span>}
    </div>
  );
}

function EventDetail({ client, e, feed, onClose, onRefresh }: { client: Client; e: NewsEvent; feed: NewsFeed; onClose: () => void; onRefresh: () => void }) {
  const [busy, setBusy] = useState<"pre" | "post" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const an = feed.analyses[e.id] ?? {};
  const rs = feed.reactions[e.id] ?? [];
  const past = new Date(e.time).getTime() < Date.now();
  const run = async (kind: "pre" | "post") => {
    setBusy(kind); setErr(null);
    try { await client.newsAnalyze(e.id, kind, !!an[kind]); onRefresh(); } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  };
  return (
    <div className={`news-detail ${e.impact}`} data-testid="news-detail">
      <div className="news-detail-head">
        <div>
          <b>{e.title}</b>
          <div className="muted small">{e.country_name} · {new Date(e.time).toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "short" })} {hm(e.time)} · impacto {IMPACT_LABEL[e.impact]}{!past && ` · ${untilText(new Date(e.time).getTime() - Date.now())}`}</div>
        </div>
        <div className="chips">
          {e.forecast && <span className="chip">previsión {e.forecast}</span>}{e.previous && <span className="chip">anterior {e.previous}</span>}
          {e.actual && <span className="chip ok" style={{ borderColor: "var(--ok)" }}>real {e.actual}</span>}
          <button className="ghost small-btn" onClick={onClose}>Cerrar</button>
        </div>
      </div>
      {!feed.ai.enabled && <p className="muted small">Sin clave de IA: los análisis son por reglas. Pon <code>ANTHROPIC_API_KEY=…</code> en <code>engine\.env</code> y reinicia el engine para que Claude ({feed.ai.model}) los haga.</p>}
      {feed.ai.enabled && feed.ai.last_error && <p className="warn small">Último error de la IA: {feed.ai.last_error}</p>}
      {err && <p className="bad small">{err}</p>}
      <div className="news-detail-grid">
        <AnalysisBox a={an.pre} kind="pre" onRedo={() => run("pre")} busy={busy === "pre"} aiOn={feed.ai.enabled} />
        {e.actual ? <AnalysisBox a={an.post} kind="post" onRedo={() => run("post")} busy={busy === "post"} aiOn={feed.ai.enabled} />
          : <div className="news-analysis post"><b>Resultado</b><p className="muted small">{past ? "El dato aún no aparece en la fuente; se comprueba cada 5 min." : "Cuando salga el dato se analizará el resultado y la reacción del precio."}</p></div>}
      </div>
      {rs.length > 0 ? rs.map((r) => <ReactionBox key={r.symbol} r={r} />) : e.actual ? <p className="muted small">Sin ticks de precio alrededor del dato (se miden con los instrumentos que opera la maestra en NinjaTrader).</p> : null}
    </div>
  );
}

export function NewsPanel({ client }: { client: Client }) {
  const [country, setCountry] = useState("USD");
  const [minImpact, setMinImpact] = useState<"low" | "medium" | "high">("low");
  const [offset, setOffset] = useState(0);            // semanas desde la actual
  const [selected, setSelected] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const feed = useNews(client, 21, country === "all" ? "" : country, minImpact, 60000, tick);
  const now = Date.now();
  const monday = useMemo(() => { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - ((d.getDay() + 6) % 7) + offset * 7); return d; }, [offset]);
  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => { const d = new Date(monday); d.setDate(monday.getDate() + i); return d; }), [monday]);
  const byDay = useMemo(() => {
    const m = new Map<string, NewsEvent[]>();
    for (const e of feed?.events ?? []) { const k = dayKey(new Date(e.time)); m.set(k, [...(m.get(k) ?? []), e]); }
    return m;
  }, [feed]);
  const nextHigh = (feed?.events ?? []).find((e) => e.impact === "high" && new Date(e.time).getTime() > now);
  const todayKey = dayKey(new Date());
  const sel = selected ? (feed?.events ?? []).find((e) => e.id === selected) : null;
  const fetched = feed?.fetched_at ? new Date(feed.fetched_at * 1000) : null;

  return (
    <div data-testid="news">
      <div className="news-bar">
        <div className="chips">
          <button className="chip-btn" onClick={() => setOffset(offset - 1)} aria-label="Semana anterior">‹</button>
          <b>{days[0].toLocaleDateString("es-ES", { day: "numeric", month: "short" })} – {days[6].toLocaleDateString("es-ES", { day: "numeric", month: "short" })}</b>
          <button className="chip-btn" onClick={() => setOffset(offset + 1)} aria-label="Semana siguiente">›</button>
          {offset !== 0 && <button className="chip-btn" onClick={() => setOffset(0)}>Esta semana</button>}
        </div>
        <div className="chips">
          <select value={country} onChange={(e) => setCountry(e.target.value)} style={{ width: "auto", padding: "6px 10px" }} data-testid="news-country">
            <option value="USD">EE. UU.</option><option value="all">Todos los países</option>
            {(feed?.countries ?? []).filter((c) => c !== "USD").map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          {(["low", "medium", "high"] as const).map((k) => <button key={k} className={`chip-btn ${minImpact === k ? "active" : ""}`} onClick={() => setMinImpact(k)}>{k === "low" ? "Todo" : k === "medium" ? "Medio y alto" : "Solo alto"}</button>)}
        </div>
      </div>
      {feed && (
        <div className="news-status muted small" data-testid="news-status">
          <span><Icon name="refresh" size={12} /> se actualiza cada {Math.round(feed.refresh_s / 60)} min{fetched ? ` · última ${hm(fetched.toISOString())}` : ""}</span>
          <span>vigilando {feed.watch.countries.length ? feed.watch.countries.join(", ") : "todos los países"} · impacto {feed.watch.min_impact === "low" ? "todos" : IMPACT_LABEL[feed.watch.min_impact] + " o más"} · aviso {feed.watch.alert_minutes} min antes</span>
          <span className={feed.ai.enabled ? "ok" : ""}>IA: {feed.ai.enabled ? `activa (${feed.ai.model})` : "sin clave · análisis por reglas"}</span>
        </div>
      )}
      {nextHigh ? (
        <div className="news-next" data-testid="news-next" onClick={() => setSelected(nextHigh.id)} role="button"><Icon name="flame" size={18} /><span className="muted small">Próximo de alto impacto</span>
          <b>{nextHigh.title}</b><span className="muted">{nextHigh.country_name} · {new Date(nextHigh.time).toLocaleDateString("es-ES", { weekday: "short" })} {hm(nextHigh.time)}</span>
          <span className="badge bad">{untilText(new Date(nextHigh.time).getTime() - now)}</span>
          {feed?.analyses[nextHigh.id]?.pre && <span className="muted small news-next-sum">{feed.analyses[nextHigh.id].pre!.data.advice}</span>}</div>
      ) : feed && !feed.error ? <p className="muted small">Sin eventos de alto impacto pendientes en los próximos días.</p> : null}
      {feed?.error && <p className="warn small">No se pudo actualizar el calendario ({feed.error}). {feed.events.length ? "Se muestra lo último descargado." : "Comprueba la conexión a Internet del PC del engine."}</p>}
      {!feed && <p className="muted small">Cargando calendario…</p>}
      {sel && feed && <EventDetail client={client} e={sel} feed={feed} onClose={() => setSelected(null)} onRefresh={() => setTick((t) => t + 1)} />}
      <div className="news-week">
        {days.map((d) => {
          const k = dayKey(d); const evs = byDay.get(k) ?? [];
          return (
            <div key={k} className={`news-day ${k === todayKey ? "today" : ""} ${d.getTime() + 86400000 < now ? "past" : ""}`}>
              <div className="news-day-head"><span>{d.toLocaleDateString("es-ES", { weekday: "short" })}</span><b>{d.getDate()}</b></div>
              {evs.length === 0 ? <div className="news-empty">{d.getDay() === 0 || d.getDay() === 6 ? "fin de semana" : "sin eventos"}</div> : (
                <ul className="news-list">
                  {evs.map((e) => { const an = feed?.analyses[e.id]; return (
                    <li key={e.id} className={`news-ev ${e.impact} ${new Date(e.time).getTime() < now ? "done" : ""} ${selected === e.id ? "sel" : ""}`} onClick={() => setSelected(selected === e.id ? null : e.id)}
                        title={`${e.title} · impacto ${IMPACT_LABEL[e.impact]}${e.forecast ? ` · previsión ${e.forecast}` : ""}${e.previous ? ` · anterior ${e.previous}` : ""} · pulsa para ver el análisis`}>
                      <i className="imp" />
                      <div><div className="t">{e.title}{(an?.pre || an?.post) && <Icon name="sparkle" size={11} />}</div>
                        <div className="m"><span>{hm(e.time)}</span>{country === "all" && <span>{e.country}</span>}{e.actual && <span className="ok">real {e.actual}</span>}{e.forecast && <span>prev. {e.forecast}</span>}{e.previous && <span>ant. {e.previous}</span>}</div></div>
                    </li>
                  ); })}
                </ul>
              )}
            </div>
          );
        })}
      </div>
      <div className="news-legend"><span><i style={{ background: "var(--bad)" }} />alto impacto</span><span><i style={{ background: "#fb923c" }} />medio</span><span><i style={{ background: "var(--warn)" }} />bajo</span><span><i style={{ background: "var(--accent)" }} />festivo</span><span><Icon name="sparkle" size={11} /> con análisis</span><span className="muted">· hora local del navegador · fuente: {feed?.source ?? "ForexFactory"}</span></div>
    </div>
  );
}

/** Métricas: cómo te va operando en la noticia frente al resto, cuánto mueve cada dato y cuánto sorprende frente a la previsión. */
export function NewsStats({ client }: { client: Client }) {
  const [days, setDays] = useState(90);
  const [st, setSt] = useState<NewsStatsT | null>(null);
  useEffect(() => { let alive = true; client.newsStats(days).then((s) => { if (alive) setSt(s); }).catch(() => {}); return () => { alive = false; }; }, [client, days]);
  if (!st) return <p className="muted small">Calculando…</p>;
  const money = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toLocaleString("es-ES", { maximumFractionDigits: 0 })} $`);
  const pc = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${Math.round(n * 100)} %`);
  const rows = [["En noticia de impacto alto", st.trades.near_high], ["En noticia de impacto medio", st.trades.near_medium], ["Fuera de noticias", st.trades.outside]] as const;
  const fs = st.follows_surprise, fsN = fs.agree + fs.disagree;
  const IMP = ["high", "medium"] as const;
  return (
    <div className="news-stats" data-testid="news-stats">
      <div className="news-bar">
        <span className="muted small">Operaciones dentro de ±{st.window_min} min de una noticia de EE. UU. de impacto medio o alto · {st.n_events} datos y {st.n_trades} operaciones en el periodo</span>
        <div className="chips">{[30, 90, 180, 365].map((d) => <button key={d} className={`chip-btn ${days === d ? "active" : ""}`} onClick={() => setDays(d)}>{d} días</button>)}</div>
      </div>
      <div className="news-stats-grid">
        <div>
          <h4>Tus operaciones</h4>
          <div className="table-wrap"><table className="chart-table"><thead><tr><th></th><th className="num">Ops.</th><th className="num">Neto</th><th className="num">Acierto</th><th className="num">Media</th><th className="num">PF</th></tr></thead>
            <tbody>{rows.map(([label, b]) => <tr key={label}><td>{label}</td><td className="num">{b.n}</td><td className={`num ${b.net > 0 ? "ok" : b.net < 0 ? "bad" : ""}`}>{money(b.net)}</td><td className="num">{pc(b.win_rate)}</td><td className="num">{money(b.avg)}</td><td className="num">{b.pf ?? "—"}</td></tr>)}</tbody></table></div>
          {st.by_title.length > 0 && <>
            <div className="muted small" style={{ marginTop: 8 }}>Por dato (con 2 o más operaciones cerca)</div>
            <div className="table-wrap"><table className="chart-table"><tbody>{st.by_title.map((b) => <tr key={b.title}><td>{b.title}</td><td className="num">{b.n}</td><td className={`num ${b.net > 0 ? "ok" : b.net < 0 ? "bad" : ""}`}>{money(b.net)}</td><td className="num">{pc(b.win_rate)}</td></tr>)}</tbody></table></div>
          </>}
          {st.trades.near_high.n + st.trades.near_medium.n > 0 && st.trades.outside.n > 0 && (st.trades.near_high.avg ?? 0) + (st.trades.near_medium.avg ?? 0) < (st.trades.outside.avg ?? 0) &&
            <p className="small news-advice"><Icon name="shield" size={13} /> Tu media por operación es peor dentro de la noticia que fuera: considera no copiar en los {st.window_min} min alrededor de los datos de impacto alto.</p>}
        </div>
        <div>
          <h4>Reacción del precio</h4>
          {IMP.some((k) => st.reactions[k]) ? (
            <div className="table-wrap"><table className="chart-table"><thead><tr><th>Impacto</th><th className="num">Datos</th><th className="num">5 min</th><th className="num">15 min</th><th className="num">30 min</th><th className="num">Rango</th><th className="num">Máx. 15</th></tr></thead>
              <tbody>{IMP.filter((k) => st.reactions[k]).map((k) => { const r = st.reactions[k]; return <tr key={k}><td>{IMPACT_LABEL[k]}</td><td className="num">{r.n}</td><td className="num">{r.avg_5m ?? "—"} %</td><td className="num">{r.avg_15m ?? "—"} %</td><td className="num">{r.avg_30m ?? "—"} %</td><td className="num">{r.avg_range ?? "—"} %</td><td className="num">{r.max_15m ?? "—"} %</td></tr>; })}</tbody></table></div>
          ) : <p className="muted small">Todavía sin reacciones medidas: se registran con los ticks de NinjaTrader en cada dato publicado (precio antes y 5/15/30 min después).</p>}
          <p className="muted small">Movimiento medio absoluto del instrumento operado tras el dato. {fsN > 0 && <>El precio fue en la dirección de la sorpresa (real &gt; previsión ⇒ sube) en {Math.round(fs.agree / fsN * 100)} % de los {fsN} datos con sorpresa.</>}</p>
          {st.movers.length > 0 && <>
            <div className="muted small">Los que más mueven (15 min)</div>
            <div className="table-wrap"><table className="chart-table"><tbody>{st.movers.map((m) => <tr key={m.title}><td>{m.title}</td><td className="num">{m.n}</td><td className="num">{m.avg_15m} %</td></tr>)}</tbody></table></div>
          </>}
        </div>
        <div>
          <h4>Sorpresas (real frente a previsión)</h4>
          {st.surprises.total.n ? (
            <>
              <div className="chips" style={{ marginBottom: 8 }}>
                <span className="chip">{st.surprises.total.n} datos</span><span className="chip ok">por encima {Math.round(st.surprises.total.mayor / st.surprises.total.n * 100)} %</span>
                <span className="chip bad">por debajo {Math.round(st.surprises.total.menor / st.surprises.total.n * 100)} %</span><span className="chip">en línea {Math.round(st.surprises.total["en línea"] / st.surprises.total.n * 100)} %</span>
              </div>
              <div className="table-wrap"><table className="chart-table"><thead><tr><th>Dato</th><th className="num">N</th><th className="num">↑</th><th className="num">↓</th><th className="num">=</th><th className="num">Desvío medio</th></tr></thead>
                <tbody>{st.surprises.by_title.map((s) => <tr key={s.title}><td>{s.title}</td><td className="num">{s.n}</td><td className="num ok">{s.beat}</td><td className="num bad">{s.miss}</td><td className="num">{s.inline}</td><td className="num">{s.avg_abs_rel === null ? "—" : `${s.avg_abs_rel.toFixed(1)} %`}</td></tr>)}</tbody></table></div>
            </>
          ) : <p className="muted small">Se acumulan con cada dato publicado que descarga el engine.</p>}
        </div>
      </div>
    </div>
  );
}

/** Resumen para Inicio: lo que queda hoy de impacto medio o alto (y lo ya pasado, atenuado). */
export function NewsToday({ client }: { client: Client }) {
  const feed = useNews(client, 2, "USD", "medium");
  const now = Date.now();
  const today = dayKey(new Date());
  const evs = (feed?.events ?? []).filter((e) => dayKey(new Date(e.time)) === today);
  if (!feed) return <p className="muted small">Cargando…</p>;
  if (feed.error && !evs.length) return <p className="muted small">Sin calendario económico ({feed.error}).</p>;
  if (!evs.length) return <p className="muted small">Hoy no hay noticias de impacto medio o alto en EE. UU.</p>;
  return (
    <ul className="news-today" data-testid="news-today">
      {evs.map((e) => { const t = new Date(e.time).getTime(); return (
        <li key={e.id} className={`${e.impact} ${t < now ? "done" : ""}`}><i className="imp" /><span className="muted">{hm(e.time)}</span><span className="nowrap" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{e.title}</span>
          <span className="muted small">{t < now ? (e.actual ? `real ${e.actual}` : "pasado") : untilText(t - now)}</span></li>
      ); })}
    </ul>
  );
}
