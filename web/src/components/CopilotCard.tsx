import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import type { CopilotState, CopilotSuggestion } from "../lib/api";
import { Card, Empty } from "./ui";

/* Copiloto en vivo: una propuesta concreta por posición abierta (IA con clave, reglas sin ella) y el botón que la ejecuta.
 * Cada botón pasa por el PIN y la confirmación; el copiloto nunca ejecuta nada solo. */
const TONE: Record<CopilotSuggestion["severity"], string> = { bad: "bad", warn: "warn", ok: "ok", info: "info" };
const newId = () => "cp" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

export function CopilotCard() {
  const { client, guard, readOnly, prices, refresh } = useStore();
  const [st, setSt] = useState<CopilotState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => client?.copilot().then(setSt).catch(() => {});
  useEffect(() => { if (!client) return; void load(); const t = window.setInterval(load, 20000); return () => window.clearInterval(t); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  const run = async () => { if (!client) return; setBusy("run"); setMsg(null); try { setSt(await client.copilotRun()); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); } };
  const toggle = async () => { if (!client || !st) return; try { setSt(await client.copilotEnabled(!st.enabled)); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } };
  const last = (symbol: string) => prices[symbol.split(" ")[0].toUpperCase()]?.last ?? 0;
  const apply = async (s: CopilotSuggestion) => {
    if (!client || !(await guard())) return;
    const key = s.account + s.symbol + s.action;
    const half = Math.max(1, Math.floor(s.quantity / 2));
    const ask = s.action === "close" ? `¿Cerrar ${s.quantity} ${s.symbol} en ${s.account} a mercado?` : s.action === "reduce" ? `¿Cerrar ${half} de ${s.quantity} ${s.symbol} en ${s.account}?`
      : s.action === "breakeven" ? `¿Llevar el stop de ${s.symbol} en ${s.account} al precio de entrada?` : `¿${s.action === "protect" ? "Poner" : "Mover"} el stop de ${s.symbol} en ${s.account} a ${s.price}?`;
    if (!confirm(ask)) return;
    setBusy(key); setMsg(null);
    try {
      if (s.action === "close" || s.action === "reduce") { const r = await client.tradeClose(newId(), s.account, s.symbol, s.action === "reduce" ? half : 0); setMsg(`${s.account} ${s.symbol}: ${s.action === "reduce" ? `cerrados ${half}` : "cerrada"} (${r.ms} ms).`); }
      else if (s.action === "breakeven") { const r = await client.tradeProtect({ client_id: newId(), account: s.account, symbol: s.symbol, breakeven: true, last_price: last(s.symbol) }); setMsg(`${s.account} ${s.symbol}: stop en ${r.avg_price} (${r.ms} ms).`); }
      else if ((s.action === "tighten" || s.action === "protect") && s.price) { const r = await client.tradeProtect({ client_id: newId(), account: s.account, symbol: s.symbol, sl_price: s.price, last_price: last(s.symbol) }); setMsg(`${s.account} ${s.symbol}: stop en ${r.sl?.price ?? s.price} (${r.ms} ms).`); }
      await refresh(); await run();
    } catch (ex) { setMsg((ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "")); } finally { setBusy(null); }
  };
  const list = st?.suggestions ?? [];
  return (
    <Card title={`Copiloto en vivo${st?.ran_at ? ` · ${st.source === "ia" ? "IA" : "reglas"} ${st.ran_at.slice(11, 16)}` : ""}`} icon="sparkle"
          right={<span className="chips"><label className="inline small"><input type="checkbox" checked={!!st?.enabled} onChange={() => void toggle()} disabled={readOnly || !st} data-testid="copilot-enabled" /> activo</label>
                 <button className="chip-btn" disabled={busy !== null} onClick={() => void run()} data-testid="copilot-run">{busy === "run" ? "…" : "Revisar ahora"}</button></span>}
          className={list.some((s) => s.severity === "bad") ? "alerts-card has-bad" : ""}>
      {!st ? <p className="muted small">Cargando…</p> : st.positions === 0 ? <Empty>Sin posiciones abiertas: el copiloto revisa cada {Math.round(st.interval_s / 60)} min mientras haya alguna.</Empty>
        : list.length === 0 ? <Empty>{st.ran_at ? "Nada que proponer: stops puestos, sin noticias cerca y sin racha." : "Aún no ha revisado: pulsa Revisar ahora."}</Empty> : (
        <ul className="alerts" data-testid="copilot-list">
          {list.map((s) => (
            <li key={s.account + s.symbol + s.action} className={`alert ${TONE[s.severity]}`} data-testid={`copilot-${s.action}`}>
              <div className="alert-main"><b>{s.account} · {s.symbol} · {s.title}</b><span className="muted">{s.detail}</span></div>
              <div className="chips">{s.action !== "hold" && <button className={`chip-btn ${s.action === "close" ? "danger" : ""}`} disabled={busy !== null || readOnly} onClick={() => void apply(s)}>{busy === s.account + s.symbol + s.action ? "…" : s.label}</button>}</div>
            </li>
          ))}
        </ul>
      )}
      {msg && <p className="small" data-testid="copilot-msg">{msg}</p>}
      {st && st.source === "regla" && list.length > 0 && <p className="muted tiny">Propuestas por reglas. Con <code>ANTHROPIC_API_KEY</code> las hace Claude con el contexto completo.</p>}
    </Card>
  );
}
