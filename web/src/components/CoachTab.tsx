import { useEffect, useState } from "react";
import type { Client, ReviewResult, AskResult } from "../lib/api";
import { Card, Empty } from "./ui";
import { Icon } from "./Icons";

/* Coach IA: revisión de la sesión (qué costó dinero, qué cambiar mañana) y chat con tus propios datos. */
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const usd = (n: number | null | undefined) => (n === null || n === undefined ? "" : `${n > 0 ? "+" : ""}${Math.round(n).toLocaleString("es-ES")} $`);

export function CoachTab({ client, aiEnabled }: { client: Client; aiEnabled: boolean }) {
  const [day, setDay] = useState(() => iso(new Date()));
  const [rev, setRev] = useState<ReviewResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [chat, setChat] = useState<{ q: string; a?: AskResult; err?: string }[]>([]);
  const [asking, setAsking] = useState(false);
  const load = async (force = false) => {
    setBusy(true); setErr(null);
    try { setRev(await client.aiReview(day, force)); } catch (ex) { setErr((ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "")); } finally { setBusy(false); }
  };
  useEffect(() => { void load(); }, [day]);   // eslint-disable-line react-hooks/exhaustive-deps
  const ask = async () => {
    const question = q.trim();
    if (!question) return;
    setQ(""); setAsking(true);
    setChat((c) => [{ q: question }, ...c]);
    try { const a = await client.aiAsk(question); setChat((c) => c.map((x, i) => (i === 0 ? { ...x, a } : x))); }
    catch (ex) { setChat((c) => c.map((x, i) => (i === 0 ? { ...x, err: (ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "") } : x))); }
    finally { setAsking(false); }
  };
  const r = rev?.review;
  const moodTone = r?.mood === "bien" ? "ok" : r?.mood === "mal" ? "bad" : "warn";
  const SUGGEST = ["¿Qué día de la semana gano más?", "¿A qué horas pierdo más dinero?", "¿Me va mejor en largos o en cortos?", "¿Qué cuenta tiene peor acierto?", "¿Cuántas operaciones hago de media al día?"];
  return (
    <>
      <div className="an-two wide-left">
        <Card title="Revisión de la sesión" icon="sparkle" right={<div className="chips"><input type="date" value={day} max={iso(new Date())} onChange={(e) => setDay(e.target.value)} data-testid="review-day" /><button className="chip-btn" disabled={busy} onClick={() => void load(true)} data-testid="review-redo">{busy ? "revisando…" : rev?.source === "ia" ? "volver a revisar" : aiEnabled ? "revisar con IA" : "recalcular"}</button></div>}>
          {err && <p className="bad small">{err}</p>}
          {!rev ? <p className="muted small">Revisando…</p> : !r ? <Empty>Sin revisión.</Empty> : (
            <div className="review" data-testid="review">
              <div className="chips">
                <span className={`badge ${rev.source === "ia" ? "accent" : ""}`}>{rev.source === "ia" ? `IA · ${rev.model}` : "por reglas"}</span>
                <span className={`badge ${moodTone}`}>disciplina {r.score}/10 · {r.mood}</span>
                <span className="muted small">{rev.stats.n} operaciones · neto {usd(rev.stats.net)} · acierto {rev.stats.win_rate !== null ? `${Math.round(rev.stats.win_rate * 100)} %` : "—"}{rev.near_news.length ? ` · ${rev.near_news.length} pegadas a una noticia` : ""}{rev.quick_stops.length ? ` · ${rev.quick_stops.length} stops en < 60 s` : ""}</span>
              </div>
              <p className="review-summary">{r.summary}</p>
              {r.patterns.length > 0 && <>
                <h4>Lo que costó (o dio) dinero</h4>
                <ul className="alerts">{r.patterns.map((p, i) => <li key={i} className={`alert ${(p.cost_usd ?? 0) < 0 ? "bad" : (p.cost_usd ?? 0) > 0 ? "ok" : "warn"}`}><div className="alert-main"><b>{p.title}{p.cost_usd !== null && p.cost_usd !== undefined ? ` · ${usd(p.cost_usd)}` : ""}</b><span className="muted">{p.evidence}</span></div></li>)}</ul>
              </>}
              <h4>Para mañana</h4>
              <ol className="review-rules">{r.tomorrow.map((t, i) => <li key={i}>{t}</li>)}</ol>
              {!aiEnabled && <p className="muted small">Sin clave de IA: esta revisión es por reglas. Con <code>ANTHROPIC_API_KEY</code> en <code>engine\.env</code> Claude lee la sesión entera (operaciones, avisos, noticias) y escribe la revisión.</p>}
              {rev.ai_error && <p className="warn small">La IA falló ({rev.ai_error}); se muestra la revisión por reglas.</p>}
            </div>
          )}
        </Card>
        <Card title="Pregunta a tus datos" icon="activity" right={<span className="muted small">últimos 90 días</span>}>
          {!aiEnabled ? <p className="muted small">Necesita <code>ANTHROPIC_API_KEY</code> en <code>engine\.env</code>. Mientras tanto, la pestaña Estadísticas tiene los mismos números por día, hora, símbolo y cuenta.</p> : (
            <>
              <form className="ask-form" onSubmit={(e) => { e.preventDefault(); void ask(); }}>
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="¿cómo me va los jueves después de las 10?" data-testid="ask-input" />
                <button className="primary small-btn" disabled={asking || !q.trim()} data-testid="ask-send">{asking ? "…" : "Preguntar"}</button>
              </form>
              <div className="chips" style={{ marginTop: 6 }}>{SUGGEST.map((s) => <button key={s} className="chip-btn" onClick={() => setQ(s)}>{s}</button>)}</div>
            </>
          )}
          {chat.length > 0 && <ul className="chat" data-testid="chat">
            {chat.map((c, i) => <li key={i}><div className="chat-q"><Icon name="users" size={12} /> {c.q}</div>
              {c.a ? <div className="chat-a">{c.a.answer}{c.a.numbers.length > 0 && <ul>{c.a.numbers.map((n, k) => <li key={k}>{n}</li>)}</ul>}<span className="muted tiny">{c.a.model} · {c.a.ms} ms · {c.a.context.n_trades} operaciones</span></div>
                : c.err ? <div className="chat-a bad">{c.err}</div> : <div className="chat-a muted">pensando…</div>}</li>)}
          </ul>}
        </Card>
      </div>
    </>
  );
}
