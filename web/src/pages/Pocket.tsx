import { useState } from "react";
import { useStore } from "../lib/store";
import { livePnl } from "../components/AccountPanel";
import { signedMoney } from "../lib/format";
import type { Route } from "../components/Shell";

/* Vista de bolsillo (#pocket): lo mínimo para el teléfono en una sola pantalla, botones grandes. P&L del día, estado de la
 * copia, posiciones con Breakeven y Cerrar, Pausar/Reanudar y CERRAR TODO. Es la pantalla que abre una notificación push. */
const newId = () => "pk" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

export function Pocket({ onRoute }: { onRoute: (r: Route) => void }) {
  const { client, accounts, risk, health, prices, wsStatus, refresh, setRisk, logout } = useStore();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const active = accounts.filter((a) => a.enabled);
  let total = 0, est = false;
  for (const a of active) { const p = livePnl(a, prices); total += p.value - (a.commissions_today || 0); est ||= p.live; }
  const positions = active.flatMap((a) => a.open_positions.filter((p) => p.quantity).map((p) => ({ a, p })));
  const run = async (key: string, fn: () => Promise<string>) => {
    if (!client) return;
    setBusy(key); setMsg(null);
    try { setMsg(await fn()); await refresh(); } catch (ex) { setMsg((ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "")); } finally { setBusy(null); }
  };
  const lastOf = (symbol: string) => prices[symbol.split(" ")[0].toUpperCase()]?.last ?? 0;
  const be = (acc: string, sym: string) => run(`be${acc}${sym}`, async () => { const r = await client!.tradeProtect({ client_id: newId(), account: acc, symbol: sym, breakeven: true, last_price: lastOf(sym) }); return `${acc} ${sym}: stop en ${r.avg_price} (${r.ms} ms).`; });
  const close = (acc: string, sym: string, q: number) => { if (!confirm(`¿Cerrar ${Math.abs(q)} ${sym} en ${acc} a mercado?`)) return; void run(`cl${acc}${sym}`, async () => { const r = await client!.tradeClose(newId(), acc, sym); return `${acc} ${sym}: cierre ${r.status} en ${r.ms} ms.`; }); };
  const closeAll = () => { if (!confirm("¿CERRAR TODO?\n\nSe cancelan las órdenes y se cierran a mercado todas las posiciones de todas las cuentas. La copia queda detenida.")) return; void run("all", async () => { setRisk(await client!.killSwitch(true, "desde la vista de bolsillo", true, true)); return "Cierre total enviado y copia detenida."; }); };
  const toggleKill = () => { const on = !risk?.kill_switch; if (on && !confirm("¿Detener la copia? No se copiará ni se operará hasta reanudar (las posiciones se quedan como están).")) return; void run("kill", async () => { setRisk(await client!.killSwitch(on, on ? "desde la vista de bolsillo" : undefined, false, false)); return on ? "Copia detenida." : "Copia reanudada."; }); };
  const live = wsStatus === "open";
  return (
    <div className="pocket" data-testid="pocket">
      <header className="pocket-head">
        <span className="brand-dot" /><b>TradePilot X</b>
        <span className={`badge ${live ? "ok" : "bad"}`}>{live ? "en vivo" : "sin conexión"}</span>
        <span className={`badge ${health?.bridge.connected ? "ok" : "bad"}`}>{health?.mode === "mock" ? "simulador" : health?.bridge.connected ? "NinjaTrader" : "sin NinjaTrader"}</span>
        <button className="link" onClick={() => onRoute("dashboard")} data-testid="pocket-full">Consola completa</button>
      </header>
      <div className="pocket-pnl">
        <span className="stat-label">P&L hoy neto · {active.length} cuenta{active.length === 1 ? "" : "s"}</span>
        <b className={`num ${total > 0 ? "ok" : total < 0 ? "bad" : ""}`} data-testid="pocket-pnl">{est && "≈ "}{signedMoney(total)}</b>
        {risk?.kill_switch && <span className="badge bad">copia detenida{risk.kill_switch_reason ? ` · ${risk.kill_switch_reason}` : ""}</span>}
        {risk?.day_closed && <span className="badge bad">día cerrado</span>}
      </div>
      <section>
        <h3>Posiciones · {positions.length}</h3>
        {positions.length === 0 ? <p className="muted">Sin posiciones abiertas.</p> : (
          <ul className="pocket-pos" data-testid="pocket-positions">
            {positions.map(({ a, p }) => (
              <li key={a.account_id + p.symbol}>
                <div className="pocket-pos-main">
                  <b>{a.alias || a.account_id}</b>
                  <span>{p.quantity > 0 ? "Largo" : "Corto"} {Math.abs(p.quantity)} {p.symbol} @ {p.avg_price}</span>
                  <span className={`num ${p.unrealized_pnl > 0 ? "ok" : p.unrealized_pnl < 0 ? "bad" : ""}`}>{signedMoney(p.unrealized_pnl)}</span>
                </div>
                <div className="pocket-pos-btns">
                  <button className="small-btn" disabled={busy !== null || !live} onClick={() => void be(a.account_id, p.symbol)}>{busy === `be${a.account_id}${p.symbol}` ? "…" : "Breakeven"}</button>
                  <button className="small-btn danger" disabled={busy !== null || !live} onClick={() => close(a.account_id, p.symbol, p.quantity)}>{busy === `cl${a.account_id}${p.symbol}` ? "…" : "Cerrar"}</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      <div className="pocket-actions">
        <button className={risk?.kill_switch ? "primary" : ""} disabled={busy !== null || !client} onClick={toggleKill} data-testid="pocket-kill">{risk?.kill_switch ? "REANUDAR COPIA" : "PAUSAR COPIA"}</button>
        <button className="danger-solid" disabled={busy !== null || !client} onClick={closeAll} data-testid="pocket-closeall">CERRAR TODO</button>
      </div>
      {msg && <p className="small pocket-msg" data-testid="pocket-msg">{msg}</p>}
      <footer className="muted tiny">
        Cuentas: {active.map((a) => `${a.alias || a.account_id} ${signedMoney(a.net_pnl)}`).join(" · ") || "—"}.<br />
        Esta pantalla se abre desde las notificaciones push. Guarda <code>#pocket</code> en la pantalla de inicio del teléfono.
        <button className="link" onClick={logout}>Salir</button>
      </footer>
    </div>
  );
}
