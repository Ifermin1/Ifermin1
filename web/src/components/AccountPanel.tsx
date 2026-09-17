import type { ReactNode } from "react";
import type { Account, AuditEvent, Drawdown, RiskLimit, Rule, WorkingOrder } from "../lib/api";
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

/** Tono del margen hasta el suelo del drawdown: rojo desde el 80 % consumido, ámbar desde el 50 %. */
export const ddTone = (d: Drawdown): "ok" | "warn" | "bad" | "muted" => d.pct === null ? "muted" : d.pct >= 80 ? "bad" : d.pct >= 50 ? "warn" : "ok";
export function DrawdownMeter({ d }: { d: Drawdown }) {
  const tone = ddTone(d);
  return (
    <span className="dd-row" title={`Consumido ${d.pct?.toFixed(0)} % del drawdown permitido (${money(d.limit)}); quedan ${money(d.room ?? 0)} hasta el suelo ${money(d.floor ?? 0)}`}>
      <span className={`meter ${tone}`}><i style={{ width: `${Math.min(100, Math.max(0, d.pct ?? 0))}%` }} /></span>
      <span className={`small ${tone}`}>{d.pct?.toFixed(0)} %{d.buffer ? <span className="muted"> · cierre a {money(d.buffer)} del suelo</span> : null}</span>
    </span>
  );
}

/** Drawdown en vivo: la equity con el último tick (si se conoce el valor del punto) y el máximo que subiría con ella. */
export function liveDrawdown(a: Account, prices: PriceMap): Drawdown {
  const d = a.drawdown; const pnl = livePnl(a, prices);
  if (!pnl.live || d.mode === "closed") return d;
  if (d.mode === "eod") {          // el suelo no se mueve intradía: solo se actualiza la equity y la caída
    const equity = a.balance + (pnl.value - a.realized_pnl);
    const room = d.floor !== null ? equity - d.floor : null;
    const pct = d.limit > 0 && room !== null ? Math.min(100, Math.max(0, ((d.limit - room) / d.limit) * 100)) : null;
    return { ...d, equity, drawdown: Math.max(0, d.peak - equity), room, pct };
  }
  const equity = a.balance + (pnl.value - a.realized_pnl);
  const peak = Math.max(d.peak, equity);
  let floor = d.floor; let locked = d.locked;
  if (d.limit > 0 && floor !== null && !locked) floor = Math.max(floor, peak - d.limit);
  const room = floor !== null ? equity - floor : null;
  const pct = d.limit > 0 && room !== null ? Math.min(100, Math.max(0, ((d.limit - room) / d.limit) * 100)) : null;
  return { ...d, equity, peak, drawdown: Math.max(0, peak - equity), floor, room, pct, locked };
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
  const gross = livePnl(a, prices);
  const fees = a.commissions_today || 0;
  const pnl = { value: gross.value - fees, live: gross.live };     // neto: lo que cuenta para el objetivo del día
  const tone = pnl.value > 0 ? "ok" : pnl.value < 0 ? "bad" : "muted";
  const posOf = (symbol: string) => a.open_positions.filter((p) => root(p.symbol) === root(symbol)).reduce((s, p) => s + p.quantity, 0);
  const orders = [...a.working_orders].sort((x, y) => orderPrice(y) - orderPrice(x));
  const conn = !a.reported ? "no reportada" : a.connected === null ? (a.connection || "sin estado") : a.connected ? "conectada" : "desconectada";
  const connTone = !a.reported || a.connected === null ? "muted" : a.connected ? "ok" : "bad";
  const dd = liveDrawdown(a, prices);
  const ddHot = dd.pct !== null && dd.pct >= 80 && !limit?.trading_halted;
  const cls = ["acct-panel", role, copying ? "copying" : "", a.desync ? "desync" : "", limit?.trading_halted ? "halted" : "", ddHot ? "dd-hot" : "", a.connected === false ? "offline" : "", compact ? "compact" : ""].join(" ");
  return (
    <article className={cls} data-account={a.account_id}>
      <header className="ap-head">
        <div className="ap-title">
          <b>{a.alias || a.account_id}</b>{a.alias && <span className="muted small">{a.account_id}</span>}
        </div>
        <div className="ap-badges">
          {role === "master" ? <span className="badge accent">maestra</span> : copying ? <span className="badge ok">copiando ×{link!.multiplier}</span> : <span className="badge muted">sin copiar</span>}
          {link?.target_root && <span className="chip">→ {link.target_root}</span>}
          {limit?.trading_halted && <span className={`badge ${limit.halted_reason === "daily_profit" ? "ok" : "bad"}`}>{limit.halted_reason === "daily_profit" ? "objetivo logrado" : limit.halted_reason === "daily_loss" ? "pérdida diaria" : limit.halted_reason === "drawdown" ? "drawdown" : "pausada"}</span>}
          {ddHot && <span className="badge bad">drawdown {dd.pct!.toFixed(0)} %</span>}
          {a.desync && <span className="badge bad">desincronizada</span>}
          <span className={`badge ${connTone}`}>{conn}</span>
        </div>
      </header>

      <div className="ap-body">
        <div className="ap-pnl">
          <span className="stat-label">P&L hoy{fees ? " neto" : ""} {pnl.live && <i className="live-dot" title="Estimado con el último precio" />}</span>
          <span className={`ap-pnl-value ${tone}`}>{pnl.live && "≈ "}{signedMoney(pnl.value)}</span>
          <span className="muted small">{money(a.balance)} · {ago(a.updated_at)}</span>
        </div>
        {(fees > 0 || a.contracts_today > 0) && (
          <div className="ap-fees muted small" data-testid="fees" title="Comisiones estimadas: contratos ejecutados hoy × tarifa por contrato y lado (Riesgo → Comisiones)">
            bruto {signedMoney(gross.value)} · comisiones <span className="bad">−{money(fees)}</span> ({a.contracts_today} contr.)
          </div>
        )}

        <div className="dd-row" data-testid="dd">
          <div className="dd-line">
            <span className="stat-label">Drawdown</span>
            <span className={`num ${dd.drawdown > 0 ? "bad" : "muted"}`}>{dd.drawdown > 0 ? `−${money(dd.drawdown)}` : "0"}</span>
            <span className="muted">desde el máximo {dd.mode === "eod" ? "EOD " : ""}{money(dd.peak)}{dd.peak_at && !compact && dd.mode !== "eod" ? ` (${time(dd.peak_at)})` : ""}</span>
            {dd.floor !== null && <span className={ddTone(dd)}>· suelo {money(dd.floor)}{dd.locked ? " (bloqueado)" : ""} · quedan {money(dd.room ?? 0)}</span>}
          </div>
          {dd.pct !== null && <DrawdownMeter d={dd} />}
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
                  {o.quantity - o.filled <= 0 && <span className="chip bad" title="No le queda nada por ejecutar: no protege nada. El addon 2.4 la cancela; si no, cancélala en NinjaTrader">0 contratos · fantasma</span>}
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
