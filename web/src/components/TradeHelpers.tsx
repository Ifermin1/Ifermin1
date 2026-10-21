import { useEffect, useRef, useState } from "react";
import type { Client, WorkingOrder } from "../lib/api";
import { money, pointValue, tickSize } from "../lib/format";
import { Icon } from "./Icons";

/* Ayudas didácticas de Operar: importes rápidos (100 en 100 en dólares, o en puntos/ticks) y el panel Proteger de una
 * posición (stop a breakeven, stop y objetivo rápidos con vista previa en dólares y ratio riesgo/beneficio). */

export type Unit = "usd" | "pts" | "ticks";
const UNIT_KEY = "tpx.trade.unit";
const UNIT_LABEL: Record<Unit, string> = { usd: "$ por contrato", pts: "puntos", ticks: "ticks" };
const CHIPS: Record<Unit, number[]> = { usd: [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000], pts: [5, 10, 15, 20, 25, 30, 40, 50, 75, 100], ticks: [4, 8, 12, 16, 20, 40, 60, 80, 100, 200] };
const loadUnit = (): Unit => { try { const u = localStorage.getItem(UNIT_KEY); return u === "pts" || u === "ticks" ? u : "usd"; } catch { return "usd"; } };

/** Valor de un tick en dólares por contrato (null si no conocemos el instrumento). */
export const tickUsd = (symbol: string): number | null => { const pv = pointValue(symbol); return pv ? pv * tickSize(symbol) : null; };
export const toTicks = (value: number, unit: Unit, symbol: string): number => {
  if (!value) return 0;
  if (unit === "ticks") return Math.round(value);
  if (unit === "pts") return Math.round(value / tickSize(symbol));
  const tu = tickUsd(symbol); return tu ? Math.round(value / tu) : 0;
};
export const fromTicks = (ticks: number, unit: Unit, symbol: string): number => {
  if (unit === "ticks") return ticks;
  if (unit === "pts") return ticks * tickSize(symbol);
  const tu = tickUsd(symbol); return tu ? ticks * tu : 0;
};
export const ticksToText = (ticks: number, symbol: string): string => {
  const pts = ticks * tickSize(symbol); const tu = tickUsd(symbol);
  return `${ticks} ticks = ${+pts.toFixed(4)} pts${tu ? ` = ${money(ticks * tu)}` : ""}`;
};
const fmtPx = (p: number, symbol: string) => { const t = tickSize(symbol); const dec = t >= 1 ? 0 : t >= 0.1 ? 1 : t >= 0.01 ? 2 : 4; return p.toFixed(dec); };

/** Selector de importe: unidad ($ por contrato / puntos / ticks), chips rápidos y un valor libre. Entrega ticks. */
export function QuickAmount({ label, symbol, ticks, onChange, testId, tone, riskTicks }: { label: string; symbol: string; ticks: number; onChange: (ticks: number) => void; testId?: string; tone?: "ok" | "bad"; riskTicks?: number }) {
  const [unit, setUnit] = useState<Unit>(loadUnit);
  useEffect(() => { try { localStorage.setItem(UNIT_KEY, unit); } catch { /* privado */ } }, [unit]);
  const noUsd = unit === "usd" && !tickUsd(symbol);
  const u: Unit = noUsd ? "pts" : unit;
  const shown = ticks ? +fromTicks(ticks, u, symbol).toFixed(u === "usd" ? 0 : 4) : "";
  return (
    <div className={`quick ${tone ?? ""}`} data-testid={testId}>
      <div className="quick-head">
        <b>{label}</b>
        <div className="view-tabs tiny-tabs">{(["usd", "pts", "ticks"] as Unit[]).map((k) => <button key={k} className={u === k ? "active" : ""} onClick={() => setUnit(k)} disabled={k === "usd" && !tickUsd(symbol)} title={k === "usd" && !tickUsd(symbol) ? "instrumento sin valor por punto conocido" : ""}>{UNIT_LABEL[k]}</button>)}</div>
        {ticks > 0 && <button className="link tiny" onClick={() => onChange(0)}>quitar</button>}
      </div>
      <div className="chips">
        {CHIPS[u].map((v) => { const t = toTicks(v, u, symbol); return <button key={v} className={`chip-btn ${t && t === ticks ? "active" : ""}`} onClick={() => onChange(t)} data-testid={testId ? `${testId}-${v}` : undefined}>{u === "usd" ? `${v} $` : v}</button>; })}
        {riskTicks ? [1, 1.5, 2, 3].map((r) => { const t = Math.round(riskTicks * r); return <button key={r} className={`chip-btn r ${t === ticks ? "active" : ""}`} onClick={() => onChange(t)} title={`objetivo = ${r} × el riesgo`}>{r}R</button>; }) : null}
        <input inputMode="decimal" className="quick-input" value={shown} placeholder="otro" onChange={(e) => onChange(toTicks(Number(e.target.value) || 0, u, symbol))} aria-label={`${label} en ${UNIT_LABEL[u]}`} />
      </div>
      <div className="muted small">{ticks ? ticksToText(ticks, symbol) : noUsd ? "este instrumento no tiene valor por punto conocido: usa puntos o ticks" : "sin " + label.toLowerCase()}</div>
    </div>
  );
}

/** Panel Proteger de una posición: stop a breakeven, stop/objetivo rápidos desde el precio medio y vista previa. */
export function ProtectPanel({ client, account, symbol, quantity, avg, last, stops, targets, isMaster, followers, copying, disabled, onDone, onClose }: {
  client: Client; account: string; symbol: string; quantity: number; avg: number; last: number | null; stops: WorkingOrder[]; targets: WorkingOrder[];
  isMaster: boolean; followers: number; copying: boolean; disabled: boolean; onDone: (msg: string) => void; onClose?: () => void }) {
  const long = quantity > 0, n = Math.abs(quantity), tick = tickSize(symbol), pv = pointValue(symbol);
  const [slTicks, setSlTicks] = useState(0);
  const [tpTicks, setTpTicks] = useState(0);
  const [beOffset, setBeOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const rnd = (x: number) => Math.round(x / tick) * tick;
  const slPrice = slTicks ? rnd(avg + (long ? -1 : 1) * slTicks * tick) : 0;
  const tpPrice = tpTicks ? rnd(avg + (long ? 1 : -1) * tpTicks * tick) : 0;
  const bePrice = rnd(avg + (long ? 1 : -1) * beOffset * tick);
  const usd = (px: number) => (pv ? (px - avg) * (long ? 1 : -1) * pv : null);
  const curStop = stops[0]?.stop_price ?? null, curTp = targets[0]?.limit_price ?? null;
  const instant = (px: number, isStop: boolean) => last !== null && px > 0 && (isStop ? (long ? px >= last : px <= last) : (long ? px <= last : px >= last));
  const beInstant = instant(bePrice, true);
  const beGain = last !== null ? (long ? last - avg : avg - last) : null;
  const newId = () => "prot" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
  const apply = async (req: { breakeven?: boolean; be_offset_ticks?: number; sl_price?: number; tp_price?: number }, what: string) => {
    setBusy(true); setErr(null);
    const send = async (force: boolean) => client.tradeProtect({ client_id: newId(), account, symbol, last_price: last ?? 0, force, ...req });
    try {
      let r;
      try { r = await send(false); }
      catch (ex) {
        const t = ex instanceof Error ? ex.message : String(ex);
        if (/al instante/.test(t) && confirm(`${t.replace(/^\d+:\s*/, "")}\n\n¿Enviar igualmente? (la posición se cerrará ya a mercado)`)) r = await send(true); else throw ex;
      }
      const parts = [r.sl ? `stop ${r.sl.action === "created" ? "creado" : r.sl.action === "modified" ? "movido" : "ya estaba"} en ${fmtPx(r.sl.price, symbol)}` : "", r.tp ? `objetivo ${r.tp.action === "created" ? "creado" : r.tp.action === "modified" ? "movido" : "ya estaba"} en ${fmtPx(r.tp.price, symbol)}` : ""].filter(Boolean);
      onDone(`${what}: ${parts.join(" · ")} · confirmado por NinjaTrader en ${r.ms} ms${isMaster ? ` · las ${followers} copias siguen` : ""}.`);
      setSlTicks(0); setTpTicks(0);
    } catch (ex) { setErr((ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "")); } finally { setBusy(false); }
  };
  return (
    <div className="protect" data-testid="protect-panel">
      <div className="protect-head">
        <b><Icon name="shield" size={14} /> Proteger {long ? "largo" : "corto"} {n} {symbol}</b>
        <span className="muted small">entrada {fmtPx(avg, symbol)}{last !== null ? ` · último ${fmtPx(last, symbol)} (${beGain !== null && pv ? money(beGain * pv * n) : "—"})` : " · sin precio en vivo"}</span>
        <span className="muted small">stop actual: {curStop !== null ? `${fmtPx(curStop, symbol)} (${usd(curStop) !== null ? money(usd(curStop)! * n) : "?"})` : "ninguno"} · objetivo: {curTp !== null ? `${fmtPx(curTp, symbol)} (${usd(curTp) !== null ? money(usd(curTp)! * n) : "?"})` : "ninguno"}</span>
        {isMaster && <span className="badge accent">maestra: se replica a {followers} copias</span>}
        {copying && <span className="badge warn" title="Esta cuenta copia a la maestra: lo que cambies aquí lo puede sobrescribir el siguiente cambio en la maestra. Mejor protege la maestra.">cuenta seguidora</span>}
        {onClose && <button className="chip-btn" style={{ marginLeft: "auto" }} onClick={onClose}>Cerrar panel</button>}
      </div>
      <div className="protect-grid">
        <div className="protect-box">
          <b>Stop a breakeven</b>
          <div className="chips">
            {[0, 1, 2, 4].map((o) => <button key={o} className={`chip-btn ${beOffset === o ? "active" : ""}`} onClick={() => setBeOffset(o)}>{o === 0 ? "en la entrada" : `+${o} tick${o > 1 ? "s" : ""}`}</button>)}
          </div>
          <div className="muted small">stop en {fmtPx(bePrice, symbol)}{beOffset && pv ? ` (asegura ${money(beOffset * tick * pv * n)})` : " (sin pérdida ni ganancia)"}{beInstant ? " · ⚠ el precio está por debajo del punto de entrada: se cerraría al instante" : ""}</div>
          <button className={`chip-btn be ${beInstant ? "danger" : ""}`} disabled={disabled || busy} onClick={() => void apply({ breakeven: true, be_offset_ticks: beOffset }, "Breakeven")} data-testid="be-apply">{curStop !== null ? "Mover el stop a breakeven" : "Poner stop en breakeven"}</button>
        </div>
        <div className="protect-box">
          <QuickAmount label="Stop" symbol={symbol} ticks={slTicks} onChange={setSlTicks} tone="bad" testId="p-sl" />
          <QuickAmount label="Objetivo" symbol={symbol} ticks={tpTicks} onChange={setTpTicks} tone="ok" testId="p-tp" riskTicks={slTicks} />
          <div className="protect-preview">
            {slTicks > 0 && <span className="bad">stop {fmtPx(slPrice, symbol)} → {usd(slPrice) !== null ? money(usd(slPrice)! * n) : "?"}{instant(slPrice, true) ? " ⚠ se dispararía ya" : ""}</span>}
            {tpTicks > 0 && <span className="ok">objetivo {fmtPx(tpPrice, symbol)} → {usd(tpPrice) !== null ? money(usd(tpPrice)! * n) : "?"}{instant(tpPrice, false) ? " ⚠ se ejecutaría ya" : ""}</span>}
            {slTicks > 0 && tpTicks > 0 && <span className="muted">ratio 1:{(tpTicks / slTicks).toFixed(1)}</span>}
            {!slTicks && !tpTicks && <span className="muted small">elige un importe para el stop y/o el objetivo; se miden desde el precio de entrada</span>}
          </div>
          <button className="chip-btn apply" disabled={disabled || busy || (!slTicks && !tpTicks)} onClick={() => void apply({ sl_price: slPrice || 0, tp_price: tpPrice || 0 }, "Protección")} data-testid="protect-apply">
            {busy ? "enviando…" : `${slTicks ? (curStop !== null ? "Mover stop" : "Poner stop") : ""}${slTicks && tpTicks ? " y " : ""}${tpTicks ? (curTp !== null ? "mover objetivo" : "poner objetivo") : ""}` || "Aplicar"}
          </button>
        </div>
      </div>
      {err && <div className="bad small">{err}</div>}
      <div className="muted small">Si existen stop u objetivo se mueven (y en la maestra las copias los siguen); si no existen se crean. Cuando se crean los dos a la vez van enlazados (OCO, addon ≥ 2.8); si la posición se cierra por otro camino, el engine cancela la salida que sobre.</div>
    </div>
  );
}

/* ---- Mover stops y objetivos con ▲ ▼ o con el teclado (+ / −, flechas), en pasos de ticks o puntos ---- */
export type Step = { unit: "ticks" | "pts"; value: number };
const STEP_KEY = "tpx.trade.step";
export const STEPS: Step[] = [{ unit: "ticks", value: 1 }, { unit: "ticks", value: 2 }, { unit: "ticks", value: 4 }, { unit: "pts", value: 1 }, { unit: "pts", value: 2 }, { unit: "pts", value: 5 }, { unit: "pts", value: 10 }, { unit: "pts", value: 25 }];
export const stepLabel = (s: Step) => `${s.value} ${s.unit === "ticks" ? (s.value === 1 ? "tick" : "ticks") : (s.value === 1 ? "pt" : "pts")}`;
export const stepPts = (s: Step, symbol: string) => (s.unit === "pts" ? s.value : s.value * tickSize(symbol));
export const loadStep = (): Step => { try { const s = JSON.parse(localStorage.getItem(STEP_KEY) || ""); return STEPS.find((x) => x.unit === s.unit && x.value === s.value) ?? STEPS[2]; } catch { return STEPS[2]; } };
export const saveStep = (s: Step) => { try { localStorage.setItem(STEP_KEY, JSON.stringify(s)); } catch { /* privado */ } };

export function StepPicker({ step, onChange }: { step: Step; onChange: (s: Step) => void }) {
  return (
    <span className="step-picker" data-testid="step-picker">
      <span className="muted small">paso</span>
      {STEPS.map((s) => <button key={stepLabel(s)} className={`chip-btn ${s.unit === step.unit && s.value === step.value ? "active" : ""}`} onClick={() => onChange(s)}>{stepLabel(s)}</button>)}
    </span>
  );
}

export type Nudgeable = { account: string; order_id: string; symbol: string; order_type: string; price: number; quantity: number };
type Pending = { price: number; timer: number; sent?: number };

/** Mueve el precio de una orden con ▲/▼: el cambio se muestra al instante y se envía 400 ms después del último toque
 *  (varios toques seguidos = una sola modificación). Devuelve el precio que se está mostrando/enviando. */
export function useNudge(client: Client, step: Step, onMsg: (m: string) => void) {
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const ref = useRef<Record<string, Pending>>({});
  const flush = (o: Nudgeable, price: number) => {
    const isStop = /^STOP/.test(o.order_type.toUpperCase());
    client.tradeModify(o.account, o.order_id, 0, isStop ? 0 : price, isStop ? price : 0)
      .then((r) => { onMsg(`${o.symbol}: ${isStop ? "stop" : "objetivo"} movido a ${fmtPx(price, o.symbol)} · ${r.ms} ms`); const cur = ref.current[o.order_id]; if (cur && cur.price === price) { cur.sent = Date.now(); setPending({ ...ref.current }); } })
      .catch((ex) => { delete ref.current[o.order_id]; setPending({ ...ref.current }); onMsg(`No se pudo mover ${o.symbol}: ${(ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "")}`); });
  };
  const nudge = (o: Nudgeable, dir: 1 | -1) => {
    const tick = tickSize(o.symbol);
    const base = ref.current[o.order_id]?.price ?? o.price;
    const price = Math.round((base + dir * stepPts(step, o.symbol)) / tick) * tick;
    const cur = ref.current[o.order_id];
    if (cur) window.clearTimeout(cur.timer);
    ref.current[o.order_id] = { price, timer: window.setTimeout(() => flush(o, price), 400) };
    setPending({ ...ref.current });
  };
  /** Precio a mostrar: el pendiente mientras el bróker no lo refleje (o hasta 4 s tras confirmarse). */
  const shown = (o: Nudgeable): { price: number; moving: boolean } => {
    const p = pending[o.order_id];
    if (!p) return { price: o.price, moving: false };
    if (Math.abs(p.price - o.price) < tickSize(o.symbol) / 2 || (p.sent && Date.now() - p.sent > 4000)) { delete ref.current[o.order_id]; return { price: o.price, moving: false }; }
    return { price: p.price, moving: !p.sent };
  };
  return { nudge, shown };
}

export function Nudger({ o, nudge, shown, disabled, step, testId, label }: { o: Nudgeable | null; nudge: (o: Nudgeable, dir: 1 | -1) => void; shown: (o: Nudgeable) => { price: number; moving: boolean }; disabled: boolean; step: Step; testId?: string; label?: string }) {
  if (!o) return <span className="muted small">{label ? `${label}: —` : "—"}</span>;
  const s = shown(o);
  return (
    <span className={`nudger ${s.moving ? "moving" : ""}`} data-testid={testId}>
      {label && <span className="muted small">{label}</span>}
      <button className="chip-btn" disabled={disabled} onClick={() => nudge(o, -1)} title={`bajar ${stepLabel(step)}`} aria-label="bajar">▼</button>
      <b>{fmtPx(s.price, o.symbol)}</b>
      <button className="chip-btn" disabled={disabled} onClick={() => nudge(o, 1)} title={`subir ${stepLabel(step)}`} aria-label="subir">▲</button>
      {s.moving && <span className="muted tiny">enviando…</span>}
    </span>
  );
}
