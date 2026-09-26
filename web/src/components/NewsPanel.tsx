import { useEffect, useMemo, useState } from "react";
import type { Client, NewsEvent, NewsFeed } from "../lib/api";
import { Icon } from "./Icons";

/* Calendario económico: la semana en siete columnas, con impacto (rojo alto, naranja medio, ámbar bajo), hora local,
 * previsión y dato anterior; filtros de país e impacto; y el próximo evento de alto impacto con cuenta atrás. */
const IMPACT_LABEL: Record<string, string> = { high: "alto", medium: "medio", low: "bajo", holiday: "festivo" };
const hm = (iso: string) => new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", hour12: false });
const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const untilText = (ms: number) => { const m = Math.round(ms / 60000); return m < 1 ? "ahora" : m < 60 ? `en ${m} min` : m < 1440 ? `en ${Math.floor(m / 60)} h ${m % 60} min` : `en ${Math.round(m / 1440)} días`; };

export function useNews(client: Client | null, days = 7, countries = "USD", minImpact = "low") {
  const [feed, setFeed] = useState<NewsFeed | null>(null);
  useEffect(() => {
    if (!client) return;
    let alive = true;
    const load = () => client.news(days, countries, minImpact).then((f) => { if (alive) setFeed(f); }).catch(() => {});
    load();
    const t = window.setInterval(load, 10 * 60000);
    return () => { alive = false; window.clearInterval(t); };
  }, [client, days, countries, minImpact]);
  return feed;
}

export function NewsPanel({ client }: { client: Client }) {
  const [country, setCountry] = useState("USD");
  const [minImpact, setMinImpact] = useState<"low" | "medium" | "high">("low");
  const [offset, setOffset] = useState(0);            // semanas desde la actual
  const feed = useNews(client, 21, country === "all" ? "" : country, minImpact);
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
      {nextHigh ? (
        <div className="news-next" data-testid="news-next"><Icon name="flame" size={18} /><span className="muted small">Próximo de alto impacto</span>
          <b>{nextHigh.title}</b><span className="muted">{nextHigh.country_name} · {new Date(nextHigh.time).toLocaleDateString("es-ES", { weekday: "short" })} {hm(nextHigh.time)}</span>
          <span className="badge bad">{untilText(new Date(nextHigh.time).getTime() - now)}</span></div>
      ) : feed && !feed.error ? <p className="muted small">Sin eventos de alto impacto pendientes en los próximos días.</p> : null}
      {feed?.error && <p className="warn small">No se pudo actualizar el calendario ({feed.error}). {feed.events.length ? "Se muestra lo último descargado." : "Comprueba la conexión a Internet del PC del engine."}</p>}
      {!feed && <p className="muted small">Cargando calendario…</p>}
      <div className="news-week">
        {days.map((d) => {
          const k = dayKey(d); const evs = byDay.get(k) ?? [];
          return (
            <div key={k} className={`news-day ${k === todayKey ? "today" : ""} ${d.getTime() + 86400000 < now ? "past" : ""}`}>
              <div className="news-day-head"><span>{d.toLocaleDateString("es-ES", { weekday: "short" })}</span><b>{d.getDate()}</b></div>
              {evs.length === 0 ? <div className="news-empty">{d.getDay() === 0 || d.getDay() === 6 ? "fin de semana" : "sin eventos"}</div> : (
                <ul className="news-list">
                  {evs.map((e) => (
                    <li key={e.id} className={`news-ev ${e.impact} ${new Date(e.time).getTime() < now ? "done" : ""}`} title={`${e.title} · impacto ${IMPACT_LABEL[e.impact]}${e.forecast ? ` · previsión ${e.forecast}` : ""}${e.previous ? ` · anterior ${e.previous}` : ""}`}>
                      <i className="imp" />
                      <div><div className="t">{e.title}</div>
                        <div className="m"><span>{hm(e.time)}</span>{country === "all" && <span>{e.country}</span>}{e.actual && <span className="ok">real {e.actual}</span>}{e.forecast && <span>prev. {e.forecast}</span>}{e.previous && <span>ant. {e.previous}</span>}</div></div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
      <div className="news-legend"><span><i style={{ background: "var(--bad)" }} />alto impacto</span><span><i style={{ background: "#fb923c" }} />medio</span><span><i style={{ background: "var(--warn)" }} />bajo</span><span><i style={{ background: "var(--accent)" }} />festivo</span><span className="muted">· hora local del navegador · fuente: {feed?.source ?? "ForexFactory"}</span></div>
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
