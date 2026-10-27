import { useState, type FormEvent } from "react";
import { useStore } from "../lib/store";
import type { AssistantPlan, AssistantReply, AssistantResult } from "../lib/api";
import { Card } from "./ui";
import { Icon } from "./Icons";

/* Asistente por chat: escribe lo que quieres; las consultas se responden al momento y lo que mueve dinero se convierte
 * en un plan que confirmas (PIN + confirmación). */
type Turn = { q: string; r?: AssistantReply; result?: AssistantResult; err?: string; cancelled?: boolean };
const SUGGEST = ["¿cuánto llevo hoy?", "posiciones", "pon todos los stops a breakeven", "cierra la mitad de NQ", "pausa la copia"];

export function AssistantCard() {
  const { client, guard, readOnly, refresh, health } = useStore();
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const ask = async (e?: FormEvent) => {
    e?.preventDefault();
    const text = q.trim();
    if (!client || !text) return;
    setQ(""); setBusy(true);
    setTurns((t) => [{ q: text }, ...t].slice(0, 12));
    try { const r = await client.assistant(text); setTurns((t) => t.map((x, i) => (i === 0 ? { ...x, r } : x))); }
    catch (ex) { setTurns((t) => t.map((x, i) => (i === 0 ? { ...x, err: (ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "") } : x))); }
    finally { setBusy(false); }
  };
  const confirm_ = async (plan: AssistantPlan, idx: number) => {
    if (!client || !(await guard()) || !confirm(`¿Ejecutar el plan?\n\n${plan.actions.map((a) => "· " + a.description).join("\n")}`)) return;
    setBusy(true);
    try { const result = await client.assistantConfirm(plan.id); setTurns((t) => t.map((x, i) => (i === idx ? { ...x, result } : x))); await refresh(); }
    catch (ex) { setTurns((t) => t.map((x, i) => (i === idx ? { ...x, err: (ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "") } : x))); }
    finally { setBusy(false); }
  };
  const cancel = async (plan: AssistantPlan, idx: number) => { try { await client?.assistantCancel(plan.id); } catch { /* ya caducado */ } setTurns((t) => t.map((x, i) => (i === idx ? { ...x, cancelled: true } : x))); };
  const aiOn = !!health?.risk && (turns[0]?.r?.source === "ia");
  return (
    <Card title="Asistente" icon="sparkle" right={<span className="muted small">{aiOn ? "con IA" : "por reglas (con clave de IA entiende cualquier frase)"} · también por Telegram</span>}>
      <form className="ask-form" onSubmit={(e) => void ask(e)}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="pon todos los stops a breakeven · cierra la mitad de NQ en Sim101 · ¿cuánto llevo hoy?" data-testid="assistant-input" />
        <button className="primary small-btn" disabled={busy || !q.trim()} data-testid="assistant-send">{busy ? "…" : "Enviar"}</button>
      </form>
      <div className="chips" style={{ marginTop: 6 }}>{SUGGEST.map((s) => <button key={s} className="chip-btn" onClick={() => setQ(s)}>{s}</button>)}</div>
      {turns.length > 0 && <ul className="chat" data-testid="assistant-chat">
        {turns.map((t, i) => (
          <li key={i}>
            <div className="chat-q"><Icon name="users" size={12} /> {t.q}</div>
            {t.err ? <div className="chat-a bad">{t.err}</div> : !t.r ? <div className="chat-a muted">pensando…</div> : (
              <div className="chat-a">
                {t.r.reply}
                {t.r.plan && !t.result && !t.cancelled && (
                  <div className="plan" data-testid="assistant-plan">
                    <ul>{t.r.plan.actions.map((a, k) => <li key={k}>{a.description}</li>)}</ul>
                    <div className="chips">
                      <button className="primary small-btn" disabled={busy || readOnly} onClick={() => void confirm_(t.r!.plan!, i)} data-testid="assistant-confirm">Ejecutar</button>
                      <button className="small-btn" disabled={busy} onClick={() => void cancel(t.r!.plan!, i)}>Cancelar</button>
                      <span className="muted tiny">caduca a las {t.r.plan.expires_at.slice(11, 16)}</span>
                    </div>
                  </div>
                )}
                {t.cancelled && <div className="muted small">Plan cancelado.</div>}
                {t.result && <ul className="plan-result" data-testid="assistant-result">{t.result.results.map((x, k) => <li key={k} className={x.ok ? "ok" : "bad"}>{x.ok ? "✔" : "✖"} {x.action} → {x.detail}</li>)}</ul>}
              </div>
            )}
          </li>
        ))}
      </ul>}
    </Card>
  );
}
