import type { ReactNode } from "react";
import type { Account, AuditEvent, RiskLimit, Rule, WorkingOrder } from "../lib/api";
import type { PriceMap } from "../lib/store";
import { ago, money, pointValue, pts, root, signedMoney, time } from "../lib/format";

/** Estimación en vivo del P&L del día: lo realizado que reporta NinjaTrader más lo flotante
 *  recalculado con el último tick (solo si conocemos el valor del punto del contrato). */
export function livePnl(a: Account, prices: PriceMap): { value: number; live: boolean } {
  let floating = 0; let live = a.open_positions.length > 0;
  for (const p of a.open_positions) {
    const px = prices[root(p.symbol)]; const pv = pointValue(p.symbol);
    if (!px || !pv || !p.avg_price) { live = false; break; }
    floating += (px.last - p.avg_price) * p.quantity * pv;
  }
  return live ? { value: a.realized_pnl + floating, live: true } : { value: a.daily_pnl, live: false };
}

const kind = (o: WorkingOrder, pos: number): "Stop" | "TP" | "Límite" | "Orden" => {
  const t = o.order_type.toUpperCase();
  if (t.startsWith("STOP")) return "Stop";
  if (t === "MIT") return "TP";
  if (t === "LIMIT") return pos !== 0 && ((pos > 0 && o.action.startsWith("SELL")) || (pos < 0 && o.action.startsWith("BUY"))) ? "TP" : "Límite";
  return "Orden";
};
const orderPrice = (o: WorkingOrder) => o.stop_price || o.limit_price;

export function AccountPanel({ a, role, link, limit, lastFill, lastReject, prices, busy, onFlatten, onResync, controls, children, compact }: {
  a: Account; role: "master" | "follower"; link?: Rule; limit?: RiskLimit; lastFill?: AuditEvent; lastReject?: AuditEvent;
  prices: PriceMap; busy?: boolean; onFlatten?: () => void; onResync?: () => void; controls?: ReactNode; children?: ReactNode; compact?: boolean;
}) {
  const copying = role === "follower" && !!link?.enabled;
  const pnl = livePnl(a, prices);
  const tone = pnl.value > 0 ? "ok" : pnl.value < 0 ? "bad" : "muted";
  const posOf = (symbol: string) => a.open_positions.filter((p) => root(p.symbol) === root(symbol)).reduce((s, p) => s + p.quantity, 0);
  const orders = [...a.working_orders].sort((x, y) => orderPrice(y) - orderPrice(x));
  const conn = !a.reported ? "no reportada" : a.connected === null ? (a.connection || "sin estado") : a.connected ? "conectada" : "desconectada";
  const connTone = !a.reported || a.connected === null ? "muted" : a.connected ? "ok" : "bad";
  const cls = ["acct-panel", role, copying ? "copying" : "", a.desync ? "desync" : "", limit?.trading_halted ? "halted" : "", a.connected === false ? "offline" : "", compact ? "compact" : ""].join(" ");
  return (
    <article className={cls} data-account={a.account_id}>
      <header className="ap-head">
        <div className="ap-title">
          <b>{a.alias || a.account_id}</b>{a.alias && <span className="muted small">{a.account_id}</span>}
        </div>
        <div className="ap-badges">
          {role === "master" ? <span className="badge accent">maestra</span> : copying ? <span className="badge ok">copiando ×{link!.multiplier}</span> : <span className="badge muted">sin copiar</span>}
          {link?.target_root && <span className="chip">→ {link.target_root}</span>}
          {limit?.trading_halted && <span className={`badge ${limit.halted_reason === "daily_profit" ? "ok" : "bad"}`}>{limit.halted_reason === "daily_profit" ? "objetivo logrado" : limit.halted_reason === "daily_loss" ? "pérdida diaria" : "pausada"}</span>}
          {a.desync && <span className="badge bad">desincronizada</span>}
          <span className={`badge ${connTone}`}>{conn}</span>
        </div>
      </header>

      <div className="ap-body">
        <div className="ap-pnl">
          <span className="stat-label">P&L hoy {pnl.live && <i className="live-dot" title="Estimado con el último precio" />}</span>
          <span className={`ap-pnl-value ${tone}`}>{pnl.live && "≈ "}{signedMoney(pnl.value)}</span>
          <span className="muted small">{money(a.balance)} · {ago(a.updated_at)}</span>
        </div>

        <div className="ap-pos">
          {a.open_positions.length === 0 ? <span className="muted">Plana{orders.length ? " · con órdenes vivas" : ""}</span> : a.open_positions.map((p) => {
            const px = prices[root(p.symbol)]; const pv = pointValue(p.symbol);
            const diff = px && p.avg_price ? (px.last - p.avg_price) * Math.sign(p.quantity) : null;
            return (
              <div key={p.symbol} className="ap-posrow">
                <span className={`side ${p.quantity > 0 ? "ok" : "bad"}`}>{p.quantity > 0 ? "LARGO" : "CORTO"} {Math.abs(p.quantity)}</span>
                <b>{p.symbol}</b>
                {p.avg_price ? <span className="muted">@ {p.avg_price}</span> : null}
                {px && <span className="muted">mercado {px.last}</span>}
                {diff !== null && <span className={`num ${diff >= 0 ? "ok" : "bad"}`}>{pts(diff)} pts{pv ? ` (${signedMoney(diff * Math.abs(p.quantity) * pv)})` : ""}</span>}
              </div>
            );
          })}
        </div>

        {orders.length > 0 && (
          <ul className="ap-orders">
            {orders.map((o) => {
              const pos = posOf(o.symbol); const k = kind(o, pos); const px = prices[root(o.symbol)]; const price = orderPrice(o);
              const dist = px && price ? price - px.last : null;
              const bad = k === "Stop" ? pos > 0 : pos < 0 && k !== "Orden";   // el stop de un largo queda por debajo del mercado: distancia negativa
              return (
                <li key={o.order_id} className={`ap-order ${k.toLowerCase()} ${o.state.toLowerCase() !== "working" ? "off" : ""}`}>
                  <span className={`tag k-${k.toLowerCase()}`}>{k}</span>
                  <span>{o.action} {o.quantity - o.filled}{o.filled ? <i className="muted"> (+{o.filled} ejec.)</i> : null} {root(o.symbol)}</span>
                  <b className="num">{price}</b>
                  {dist !== null && <span className={`num small ${(bad ? -dist : dist) >= 0 ? "muted" : "warn"}`}>{pts(dist)} pts del mercado</span>}
                  {o.state.toLowerCase() !== "working" && <span className="chip">{o.state}</span>}
                </li>
              );
            })}
          </ul>
        )}

        {(lastReject && (!lastFill || lastReject.timestamp > lastFill.timestamp))
          ? <div className="ap-last bad">Rechazo {time(lastReject.timestamp)}: {lastReject.message.replace(`${a.account_id}: `, "")}</div>
          : lastFill && <div className="ap-last muted">Último fill {time(lastFill.timestamp)}: {lastFill.message.replace(`${a.account_id}: `, "")}</div>}
        {copying && a.connected === false && <div className="ap-last warn">Copiando pero desconectada: NinjaTrader rechazará las órdenes hasta que conecte.</div>}
        {a.desync && (
          <div className="ap-last bad">Desincronizada: {a.desync_detail}. Solo se copian salidas hasta igualarla.
            {onResync && <button className="ghost small-btn" disabled={busy} onClick={onResync}>Igualar a la maestra</button>}
          </div>
        )}
      </div>

      {(controls || onFlatten) && (
        <footer className="ap-actions">
          {controls}
          {onFlatten && (a.open_positions.length > 0 || orders.length > 0) && (
            <button className="danger-solid small-btn" disabled={busy} onClick={onFlatten} title="Cancelar órdenes y cerrar posición a mercado">Cerrar</button>
          )}
        </footer>
      )}
      {children}
    </article>
  );
}
