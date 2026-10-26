import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../lib/store";
import type { EntryCheck, ManualOrder, ManualOrderResult, TradeStatus, TradeTemplate, Trails, TrailSpec, WorkingOrder } from "../lib/api";
import { CandleChart } from "../components/CandleChart";
import { Card, Empty } from "../components/ui";
import { Icon } from "../components/Icons";
import { root, signedMoney, tickSize, pointValue } from "../lib/format";
import { QuickAmount, ProtectPanel, ticksToText, StepPicker, Nudger, useNudge, loadStep, saveStep, stepLabel, trailText, STEPS, type Nudgeable, type Step } from "../components/TradeHelpers";

/* Operar: poner, modificar y cancelar órdenes y cerrar posiciones desde cualquier sitio.
 * Pensado para que un retraso nunca cueste dinero: cada orden lleva un id único (reenviarla nunca duplica), solo se marca
 * aceptada cuando NinjaTrader la confirma, se avisa en rojo si no confirma, y los botones se bloquean sin conexión en vivo. */

const TYPES = [["MARKET", "Mercado"], ["LIMIT", "Límite"], ["STOPMARKET", "Stop"], ["STOPLIMIT", "Stop límite"]] as const;
type OrderType = (typeof TYPES)[number][0];
type Sent = { cid: string; order: ManualOrder; state: "sending" | "accepted" | "filled" | "unconfirmed" | "rejected"; ms?: number; detail?: string; at: number; orderId?: string };
const newId = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID().replace(/-/g, "").slice(0, 20) : Math.random().toString(36).slice(2) + Date.now().toString(36));
const CONFIRM_KEY = "tpx.trade.confirm", SYMBOL_KEY = "tpx.trade.symbol", QTY_KEY = "tpx.trade.qty";
const load = (k: string, d: string) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const save = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* privado */ } };
const fmtPx = (p: number, symbol: string) => { const t = tickSize(symbol); const dec = t >= 1 ? 0 : t >= 0.1 ? 1 : t >= 0.01 ? 2 : 4; return p.toFixed(dec); };
const hhmmss = (t: number) => new Date(t).toLocaleTimeString("es-ES", { hour12: false });

export function Trade() {
  const { client, accounts, health, rules, prices, wsStatus, wsRtt, wsLastAt, audit, risk, guard, readOnly } = useStore();
  const master = health?.bridge.master_account ?? null;
  const [status, setStatus] = useState<TradeStatus | null>(null);
  const [trails, setTrails] = useState<Trails | null>(null);
  const [trailTick, setTrailTick] = useState(0);
  useEffect(() => {
    if (!client) return;
    let alive = true;
    const load = () => client.trails().then((t) => { if (alive) setTrails(t); }).catch(() => {});
    load();
    const t = window.setInterval(load, 2000);
    return () => { alive = false; window.clearInterval(t); };
  }, [client, trailTick]);
  const trailOf = (acc: string, sym: string) => trails?.trails.find((t) => t.account.toLowerCase() === acc.toLowerCase() && t.symbol.toLowerCase() === sym.toLowerCase()) ?? null;
  const enabledAccounts = accounts.filter((a) => a.enabled);
  const [account, setAccount] = useState("");
  const [symbol, setSymbol] = useState(() => load(SYMBOL_KEY, ""));
  const [custom, setCustom] = useState(false);
  const [type, setType] = useState<OrderType>("MARKET");
  const [qty, setQty] = useState(() => Math.max(1, Number(load(QTY_KEY, "1")) || 1));
  const [limit, setLimit] = useState("");
  const [stop, setStop] = useState("");
  const [sl, setSl] = useState("");
  const [tp, setTp] = useState("");
  const [confirmFirst, setConfirmFirst] = useState(() => load(CONFIRM_KEY, "1") === "1");
  const [trailMode, setTrailMode] = useState<"" | "fixed" | "adaptive" | "ai">("");      // trailing que se activa solo al ejecutarse
  const [oco, setOco] = useState(false);                                                    // OCO de entrada: stop de compra arriba y de venta abajo
  const [ocoUp, setOcoUp] = useState("");
  const [ocoDown, setOcoDown] = useState("");
  const [templates, setTemplates] = useState<TradeTemplate[]>([]);
  useEffect(() => { if (client) client.templates().then((r) => setTemplates(r.templates)).catch(() => {}); }, [client]);
  const trailSpec = (): TrailSpec | null => (trailMode ? { mode: trailMode, distance_ticks: Number(sl) || 40, step_ticks: 2, atr_k: 1.5, min_ticks: Math.max(2, Math.round((Number(sl) || 40) / 5)), max_ticks: Math.max((Number(sl) || 40) * 4, 40) } : null);
  const applyTemplate = (t: TradeTemplate) => { if (t.symbol) setSymbol(t.symbol); setQty(t.quantity); setType(t.order_type); setSl(t.sl_ticks ? String(t.sl_ticks) : ""); setTp(t.tp_ticks ? String(t.tp_ticks) : ""); setTrailMode(t.trail?.mode ?? ""); setOco(false); setMsg(`Plantilla ${t.name} cargada: revisa y pulsa COMPRAR o VENDER.`); };
  const saveTemplate = async () => {
    if (!client) return;
    const name = prompt("Nombre de la plantilla (instrumento, cantidad, tipo, stop/objetivo y trailing actuales):", `${root(symbol)} ${qty} ${TYPES.find((t) => t[0] === type)?.[1]}`);
    if (!name) return;
    const t: TradeTemplate = { id: name.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 24) || `t${Date.now().toString(36)}`, name: name.slice(0, 40), symbol, quantity: qty, order_type: type, sl_ticks: Number(sl) || 0, tp_ticks: Number(tp) || 0, trail: trailSpec() };
    try { const r = await client.saveTemplates([...templates.filter((x) => x.id !== t.id), t]); setTemplates(r.templates); setMsg(`Plantilla ${t.name} guardada.`); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); }
  };
  const deleteTemplate = async (t: TradeTemplate) => { if (!client || !confirm(`¿Borrar la plantilla ${t.name}?`)) return; const r = await client.saveTemplates(templates.filter((x) => x.id !== t.id)); setTemplates(r.templates); };
  const submitOco = async () => {
    if (!client || !account || !symbol || !canTrade) return;
    const up = Number(ocoUp) || 0, down = Number(ocoDown) || 0;
    if (!up || !down || up <= down) { setMsg("Indica el stop de compra por encima y el de venta por debajo del precio."); return; }
    if (!(await guard())) return;
    if (confirmFirst && !confirm(`¿Enviar OCO de entrada en ${account}?\n\nCOMPRA ${qty} ${symbol} stop ${up} / VENTA ${qty} ${symbol} stop ${down}${Number(sl) ? ` · SL ${sl} ticks` : ""}${Number(tp) ? ` · TP ${tp} ticks` : ""}${trailMode ? ` · trailing ${trailMode}` : ""}\nLa que ejecute cancela la otra.`)) return;
    setBusy("oco"); setMsg(null);
    try {
      const r = await client.tradeOco({ client_id: newId(), account, symbol, quantity: qty, sl_ticks: Number(sl) || 0, tp_ticks: Number(tp) || 0, trail: trailSpec(),
        legs: [{ action: "BUY", order_type: "STOPMARKET", stop_price: up }, { action: "SELL", order_type: "STOPMARKET", stop_price: down }] });
      for (const leg of r.legs) track({ cid: leg.client_id, order: { client_id: leg.client_id, account, symbol, action: leg.action as "BUY" | "SELL", quantity: qty, order_type: "STOPMARKET", stop_price: leg.action === "BUY" ? up : down }, state: "accepted", ms: leg.ms, detail: leg.detail, at: Date.now(), orderId: leg.order_id });
      setMsg(`OCO aceptada por NinjaTrader (${r.legs.map((l) => l.ms).join(" / ")} ms)${r.trail_pending ? " · el trailing arrancará al ejecutarse" : ""}.`);
    } catch (ex) { setMsg((ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "")); } finally { setBusy(null); }
  };
  const [sent, setSent] = useState<Sent[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [step, setStepState] = useState<Step>(loadStep);
  const setStep = (st: Step) => { setStepState(st); saveStep(st); };
  const [selOrder, setSelOrder] = useState<string | null>(null);     // orden seleccionada para mover con el teclado
  const timers = useRef<Record<string, number>>({});

  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t); }, []);
  useEffect(() => {
    if (!client) return;
    let alive = true;
    const load = () => client.tradeStatus().then((s) => { if (alive) setStatus(s); }).catch(() => {});
    load();
    const t = window.setInterval(load, 5000);
    return () => { alive = false; window.clearInterval(t); };
  }, [client]);
  useEffect(() => { if (!account && (master || enabledAccounts.length)) setAccount(master ?? enabledAccounts[0].account_id); }, [master, account, enabledAccounts.length]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { save(SYMBOL_KEY, symbol); }, [symbol]);
  useEffect(() => { save(QTY_KEY, String(qty)); }, [qty]);
  useEffect(() => { save(CONFIRM_KEY, confirmFirst ? "1" : "0"); }, [confirmFirst]);
  // ejecuciones de órdenes manuales: el addon publica MANUAL_STATUS → auditoría MANUAL_FILL con el id
  useEffect(() => {
    for (const a of audit.slice(0, 20)) {
      const id = (a.details as { manual_id?: string; client_id?: string } | null)?.manual_id ?? (a.details as { client_id?: string } | null)?.client_id;
      if (!id) continue;
      if (a.event_type === "MANUAL_FILL") setSent((l) => l.map((s) => (s.cid === id && s.state !== "filled" ? { ...s, state: "filled" } : s)));
      if (a.event_type === "MANUAL_REJECTED") setSent((l) => l.map((s) => (s.cid === id && s.state === "accepted" ? { ...s, state: "rejected", detail: a.message } : s)));
    }
  }, [audit]);

  const followersOf = (acc: string) => rules.filter((r) => r.enabled && r.master_account.toLowerCase() === acc.toLowerCase()).length;
  const symbols = useMemo(() => {
    const set = new Set<string>();
    for (const p of Object.values(prices)) set.add(p.symbol);
    for (const a of accounts) { for (const p of a.open_positions) set.add(p.symbol); for (const o of a.working_orders) set.add(o.symbol); }
    for (const s of status?.symbols ?? []) set.add(s);
    if (symbol) set.add(symbol);
    return [...set].sort();
  }, [prices, accounts, symbol, status]);
  useEffect(() => { if (!symbol && symbols.length) setSymbol(symbols[0]); }, [symbols, symbol]);
  const last = prices[root(symbol)]?.last ?? null;
  const tick = tickSize(symbol || "ES");
  const stale = wsStatus !== "open" || (wsLastAt > 0 && now - wsLastAt > 20000);
  const canTrade = !!client && !stale && !!status?.ready && !readOnly;
  const canClose = !!client && !stale && !!status && (status.ready || /kill switch/i.test(status.reason ?? "")) && !readOnly;
  const blockReason = !client ? "sin sesión" : readOnly ? "token de solo lectura: esta sesión no puede operar" : wsStatus !== "open" ? "sin conexión en vivo con el engine (reconectando…)" : stale ? "el engine no envía datos desde hace más de 20 s" : status?.reason ?? null;

  const bump = (v: string, dir: number, setter: (s: string) => void) => { const base = Number(v) || last || 0; setter(fmtPx(Math.round((base + dir * tick) / tick) * tick, symbol)); };
  const track = (s: Sent) => setSent((l) => [s, ...l.filter((x) => x.cid !== s.cid)].slice(0, 30));
  const send = async (order: ManualOrder, closing = false) => {
    if (!client) return;
    setMsg(null);
    track({ cid: order.client_id, order, state: "sending", at: Date.now() });
    setBusy(order.client_id);
    timers.current[order.client_id] = window.setTimeout(() => setSent((l) => l.map((s) => (s.cid === order.client_id && s.state === "sending" ? { ...s, state: "unconfirmed", detail: "sin respuesta del engine en 10 s" } : s))), 10000);
    try {
      const r: ManualOrderResult = closing ? await client.tradeClose(order.client_id, order.account, order.symbol, order.quantity) : await client.tradeOrder(order);
      window.clearTimeout(timers.current[order.client_id]);
      setSent((l) => l.map((s) => (s.cid === order.client_id ? { ...s, state: r.status === "filled" ? "filled" : "accepted", ms: r.ms, detail: r.detail, orderId: r.order_id } : s)));
    } catch (ex) {
      window.clearTimeout(timers.current[order.client_id]);
      const text = ex instanceof Error ? ex.message : String(ex);
      const unconfirmed = /no confirm|Failed to fetch|NetworkError|timeout/i.test(text);
      setSent((l) => l.map((s) => (s.cid === order.client_id ? { ...s, state: unconfirmed ? "unconfirmed" : "rejected", detail: text.replace(/^\d+:\s*/, "") } : s)));
    } finally { setBusy(null); }
  };
  const [check, setCheck] = useState<EntryCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkAlways, setCheckAlways] = useState(() => load("tpx.trade.check", "1") === "1");
  useEffect(() => { save("tpx.trade.check", checkAlways ? "1" : "0"); }, [checkAlways]);
  const runCheck = async (action: "BUY" | "SELL"): Promise<EntryCheck | null> => {
    if (!client || !account || !symbol) return null;
    setChecking(true);
    try { const c = await client.aiCheckEntry({ account, symbol, action, quantity: qty, order_type: type, sl_ticks: Number(sl) || 0, tp_ticks: Number(tp) || 0 }, false); setCheck(c); return c; }
    catch { return null; } finally { setChecking(false); }
  };
  const submit = async (action: "BUY" | "SELL") => {
    if (!account || !symbol || !canTrade) return;
    if (!(await guard())) return;
    if (checkAlways) {
      const c = await runCheck(action);
      if (c && c.verdict === "red" && !confirm(`SEMÁFORO EN ROJO:\n\n${c.reasons.map((r) => "· " + r.text).join("\n")}\n\n¿Enviar igualmente?`)) return;
    }
    const order: ManualOrder = { client_id: newId(), account, symbol, action, quantity: qty, order_type: type,
      limit_price: type === "LIMIT" || type === "STOPLIMIT" ? Number(limit) || 0 : 0, stop_price: type === "STOPMARKET" || type === "STOPLIMIT" ? Number(stop) || 0 : 0,
      sl_ticks: Number(sl) || 0, tp_ticks: Number(tp) || 0, trail: trailSpec() };
    if ((order.order_type === "LIMIT" || order.order_type === "STOPLIMIT") && !order.limit_price) { setMsg("Indica el precio límite."); return; }
    if ((order.order_type === "STOPMARKET" || order.order_type === "STOPLIMIT") && !order.stop_price) { setMsg("Indica el precio del stop."); return; }
    const desc = `${action === "BUY" ? "COMPRAR" : "VENDER"} ${qty} ${symbol} ${TYPES.find((t) => t[0] === type)?.[1]}${order.limit_price ? ` @ ${order.limit_price}` : ""}${order.stop_price ? ` stop ${order.stop_price}` : ""}${order.sl_ticks ? ` · SL ${order.sl_ticks} ticks` : ""}${order.tp_ticks ? ` · TP ${order.tp_ticks} ticks` : ""}${trailMode ? ` · trailing ${trailMode} al ejecutarse` : ""}\nen ${account}${account === master ? ` (MAESTRA: se copia a ${followersOf(account)} seguidoras)` : ""}`;
    if (confirmFirst && !confirm(`¿Enviar?\n\n${desc}`)) return;
    void send(order);
  };
  const retry = (s: Sent) => void send(s.order, s.order.note === "close");
  const closePos = async (acc: string, sym: string, q: number, part?: number) => {
    const n = part && part > 0 && part < Math.abs(q) ? part : Math.abs(q);
    if (!canClose || !(await guard())) return;
    if (!confirm(n < Math.abs(q) ? `¿Cerrar ${n} de ${Math.abs(q)} ${sym} en ${acc} a mercado? El stop y el objetivo se ajustan a los ${Math.abs(q) - n} que quedan.` : `¿Cerrar ${Math.abs(q)} ${sym} en ${acc} a mercado? Se cancelan su stop y su objetivo.`)) return;
    void send({ client_id: newId(), account: acc, symbol: sym, action: q > 0 ? "SELL" : "BUY", quantity: n, order_type: "MARKET", note: "close" }, true);
  };
  const closeAsk = (acc: string, sym: string, q: number) => { const v = prompt(`¿Cuántos contratos cerrar de ${Math.abs(q)} ${sym}?`, String(Math.max(1, Math.floor(Math.abs(q) / 2)))); const n = Number(v); if (n > 0) void closePos(acc, sym, q, n); };
  const flattenAcc = async (acc: string) => {
    if (!client || !(await guard())) return;
    if (!confirm(`¿CERRAR TODO en ${acc}?\n\nSe cancelan sus órdenes y se cierran todas sus posiciones a mercado.`)) return;
    setBusy("flat-" + acc);
    try { await client.flatten(acc, "desde Operar"); setMsg(`${acc}: cierre enviado.`); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  };
  const cancel = async (acc: string, oid: string) => {
    if (!client || !canClose) return;
    setBusy(oid);
    try { const r = await client.tradeCancel(acc, oid); setMsg(`Cancelación confirmada en ${r.ms} ms.`); } catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  };
  const modify = async (acc: string, oid: string, otype: string, price: number) => {
    if (!client || !canClose || !price) return;
    setBusy(oid);
    const isStop = otype === "STOPMARKET";
    try { const r = await client.tradeModify(acc, oid, 0, isStop ? 0 : price, isStop ? price : 0); setMsg(`Modificación confirmada en ${r.ms} ms.`); setEdit((e) => { const n = { ...e }; delete n[oid]; return n; }); }
    catch (ex) { setMsg(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  };

  const positions = accounts.filter((a) => a.enabled).flatMap((a) => a.open_positions.filter((p) => p.quantity).map((p) => ({ ...p, account: a.account_id })));
  const [protect, setProtect] = useState<string | null>(null);
  const exitOrders = (acc: string, sym: string, q: number): { stops: WorkingOrder[]; targets: WorkingOrder[] } => {
    const exitAction = q > 0 ? "SELL" : "BUY";
    const own = (accounts.find((a) => a.account_id === acc)?.working_orders ?? []).filter((o) => o.symbol.toLowerCase() === sym.toLowerCase() && o.action.toUpperCase().startsWith(exitAction) && !/filled|cancelled|rejected/i.test(o.state));
    return { stops: own.filter((o) => /^STOP/.test(o.order_type.toUpperCase())), targets: own.filter((o) => o.order_type.toUpperCase() === "LIMIT") };
  };
  const orders = accounts.filter((a) => a.enabled).flatMap((a) => a.working_orders.map((o) => ({ ...o, account: a.account_id })));
  const { nudge, shown } = useNudge(client!, step, (m) => setMsg(m));
  const asNudge = (o: { account: string; order_id: string; symbol: string; order_type: string; quantity: number; stop_price: number; limit_price: number }): Nudgeable =>
    ({ account: o.account, order_id: o.order_id, symbol: o.symbol, order_type: o.order_type, quantity: o.quantity, price: /^STOP/.test(o.order_type.toUpperCase()) ? o.stop_price : o.limit_price || o.stop_price });
  // teclado: con una orden seleccionada, + / ▲ sube y − / ▼ baja un paso; ◀ ▶ cambian el paso; Esc quita la selección
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && /INPUT|SELECT|TEXTAREA/.test(t.tagName)) return;
      if (e.key === "Escape") { setSelOrder(null); return; }
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") { const i = STEPS.findIndex((x) => x.unit === step.unit && x.value === step.value); setStep(STEPS[(i + (e.key === "ArrowRight" ? 1 : STEPS.length - 1)) % STEPS.length]); e.preventDefault(); return; }
      const dir = e.key === "+" || e.key === "ArrowUp" ? 1 : e.key === "-" || e.key === "ArrowDown" ? -1 : 0;
      if (!dir || !selOrder || !canClose) return;
      const o = orders.find((x) => x.order_id === selOrder);
      if (o) { nudge(asNudge(o), dir); e.preventDefault(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });
  const livePnl = (sym: string, q: number, avg: number, fallback: number) => { const px = prices[root(sym)]?.last; const pv = pointValue(sym); return px && pv ? (px - avg) * q * pv : fallback; };
  const lat = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v)} ms`);
  const latTone = (v: number | null | undefined, warn: number, bad: number) => (v === null || v === undefined ? "muted" : v < warn ? "ok" : v < bad ? "warn" : "bad");

  if (!client) return null;
  return (
    <div className="grid" data-testid="trade">
      <Card>
        <div className="trade-status" data-testid="trade-status">
          <span className={`badge ${stale ? "bad" : "ok"}`}>{stale ? "SIN CONEXIÓN EN VIVO" : "en vivo"}</span>
          <span className={latTone(wsRtt, 150, 400)} title="Ida y vuelta entre este navegador y el engine por el WebSocket"><Icon name="activity" size={13} /> consola ↔ engine {lat(wsRtt)}</span>
          <span className={latTone(status?.ping_ms, 50, 200)} title="Ida y vuelta entre el engine y el addon de NinjaTrader (PING)"><Icon name="bolt" size={13} /> engine ↔ NinjaTrader {lat(status?.ping_ms)}</span>
          <span className={latTone(status?.last_order_ms, 300, 1000)} title="Última orden manual: desde que el engine la manda hasta que NinjaTrader la confirma"><Icon name="clock" size={13} /> última orden {lat(status?.last_order_ms)}</span>
          {status?.heartbeat_age_s !== null && status?.heartbeat_age_s !== undefined && <span className={status.heartbeat_age_s < 10 ? "muted" : "bad"}>latido del addon hace {Math.round(status.heartbeat_age_s)} s</span>}
          <span className="muted small">addon {status?.addon_version ?? (status?.mode === "mock" ? "simulador" : "—")}{status && status.mode !== "mock" ? ` (requiere ≥ ${status.required_addon})` : ""}</span>
          {status && <span className="muted small">enviadas {status.stats.orders} · rechazadas {status.stats.rejected} · sin confirmar {status.stats.unconfirmed}</span>}
        </div>
        {blockReason && <div className={`banner ${canClose ? "warn" : "bad"}`} data-testid="trade-block">{canClose ? "Solo se permite cerrar: " : "No se puede operar: "}{blockReason}</div>}
        {risk?.kill_switch && <div className="muted small">Kill switch activo: las entradas están bloqueadas; cerrar y cancelar siguen funcionando.</div>}
      </Card>

      <div className="an-two wide-left">
        <Card title="Nueva orden" icon="bolt" right={<label className="inline small"><input type="checkbox" checked={confirmFirst} onChange={(e) => setConfirmFirst(e.target.checked)} /> pedir confirmación</label>}>
          <div className="ticket" data-testid="ticket">
            <label>Cuenta
              <select value={account} onChange={(e) => setAccount(e.target.value)} data-testid="t-account">
                {enabledAccounts.map((a) => <option key={a.account_id} value={a.account_id}>{a.alias || a.account_id}{a.account_id === master ? ` · MAESTRA (se copia a ${followersOf(a.account_id)})` : ""}</option>)}
              </select>
            </label>
            <label>Instrumento
              {custom ? <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="p. ej. NQ 12-26 (como en NinjaTrader)" data-testid="t-symbol-input" onBlur={() => setCustom(false)} autoFocus />
                : <select value={symbol} onChange={(e) => { if (e.target.value === "__custom") { setCustom(true); } else setSymbol(e.target.value); }} data-testid="t-symbol">
                  {symbols.map((s) => <option key={s} value={s}>{s}</option>)}<option value="__custom">Otro…</option>
                </select>}
            </label>
            {(templates.length > 0 || symbol) && <div className="chips" data-testid="templates">
              <span className="muted small">plantillas</span>
              {templates.map((t) => <span key={t.id} className="tpl"><button className="chip-btn" onClick={() => applyTemplate(t)} title={`${t.symbol || "cualquier instrumento"} · ${t.quantity} · ${t.order_type}${t.sl_ticks ? ` · SL ${t.sl_ticks} t` : ""}${t.tp_ticks ? ` · TP ${t.tp_ticks} t` : ""}${t.trail ? ` · trailing ${t.trail.mode}` : ""}`}>{t.name}</button><button className="link tiny" onClick={() => void deleteTemplate(t)} aria-label={`borrar ${t.name}`}>✕</button></span>)}
              <button className="link tiny" onClick={() => void saveTemplate()} data-testid="tpl-save">guardar la actual como plantilla</button>
            </div>}
            <div className="ticket-row">
              <div className="view-tabs" data-testid="t-type">{TYPES.map(([k, l]) => <button key={k} className={`${type === k && !oco ? "active" : ""}`} onClick={() => { setType(k); setOco(false); }}>{l}</button>)}<button className={oco ? "active" : ""} onClick={() => setOco(true)} data-testid="t-oco">OCO</button></div>
              <div className="qty"><button className="chip-btn" onClick={() => setQty(Math.max(1, qty - 1))} aria-label="menos">−</button>
                <input type="number" min={1} max={status?.max_qty ?? 20} value={qty} onChange={(e) => setQty(Math.max(1, Math.min(status?.max_qty ?? 20, Number(e.target.value) || 1)))} data-testid="t-qty" />
                <button className="chip-btn" onClick={() => setQty(Math.min(status?.max_qty ?? 20, qty + 1))} aria-label="más">+</button><span className="muted small">contratos</span></div>
            </div>
            {(type === "LIMIT" || type === "STOPLIMIT") && <div className="ticket-row"><label>Precio límite<div className="px"><button className="chip-btn" onClick={() => bump(limit, -1, setLimit)}>−</button><input inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder={last ? fmtPx(last, symbol) : ""} data-testid="t-limit" /><button className="chip-btn" onClick={() => bump(limit, 1, setLimit)}>+</button>{last && <button className="chip-btn" onClick={() => setLimit(fmtPx(last, symbol))}>último</button>}</div></label></div>}
            {(type === "STOPMARKET" || type === "STOPLIMIT") && <div className="ticket-row"><label>Precio del stop<div className="px"><button className="chip-btn" onClick={() => bump(stop, -1, setStop)}>−</button><input inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)} placeholder={last ? fmtPx(last, symbol) : ""} data-testid="t-stop" /><button className="chip-btn" onClick={() => bump(stop, 1, setStop)}>+</button>{last && <button className="chip-btn" onClick={() => setStop(fmtPx(last, symbol))}>último</button>}</div></label></div>}
            {oco && <div className="ticket-row oco-row" data-testid="oco-row">
              <label>Compra stop por encima<div className="px"><button className="chip-btn" onClick={() => bump(ocoUp, -1, setOcoUp)}>−</button><input inputMode="decimal" value={ocoUp} onChange={(e) => setOcoUp(e.target.value)} placeholder={last ? fmtPx(last + 8 * tick, symbol) : ""} data-testid="oco-up" /><button className="chip-btn" onClick={() => bump(ocoUp, 1, setOcoUp)}>+</button>{last && [4, 8, 20].map((k) => <button key={k} className="chip-btn" onClick={() => setOcoUp(fmtPx(last + k * tick, symbol))}>+{k} t</button>)}</div></label>
              <label>Venta stop por debajo<div className="px"><button className="chip-btn" onClick={() => bump(ocoDown, -1, setOcoDown)}>−</button><input inputMode="decimal" value={ocoDown} onChange={(e) => setOcoDown(e.target.value)} placeholder={last ? fmtPx(last - 8 * tick, symbol) : ""} data-testid="oco-down" /><button className="chip-btn" onClick={() => bump(ocoDown, 1, setOcoDown)}>+</button>{last && [4, 8, 20].map((k) => <button key={k} className="chip-btn" onClick={() => setOcoDown(fmtPx(last - k * tick, symbol))}>−{k} t</button>)}</div></label>
              <span className="muted small">Rotura en cualquier dirección: la orden que ejecute cancela la otra; el stop y el objetivo automáticos se aplican a la que entre.</span>
            </div>}
            {<div className="brackets" data-testid="t-brackets">
              <QuickAmount label="Stop automático" symbol={symbol} ticks={Number(sl) || 0} onChange={(t) => setSl(t ? String(t) : "")} testId="t-sl" tone="bad" />
              <QuickAmount label="Objetivo automático" symbol={symbol} ticks={Number(tp) || 0} onChange={(t) => setTp(t ? String(t) : "")} testId="t-tp" tone="ok" riskTicks={Number(sl) || 0} />
              <div className="chips" data-testid="t-trail"><span className="muted small">trailing al ejecutarse</span>
                {([["", "ninguno"], ["fixed", "fijo"], ["adaptive", "adaptativo"], ["ai", "IA"]] as const).map(([k, l]) => <button key={k} className={`chip-btn ${trailMode === k ? "active" : ""}`} disabled={k === "ai" && !status?.ai_enabled} onClick={() => setTrailMode(k)}>{l}</button>)}
                {trailMode && <span className="muted small">distancia inicial = el stop ({Number(sl) || 40} ticks); se ajusta luego en Proteger</span>}</div>
              <span className="muted small">{Number(sl) || Number(tp) ? `Por contrato: ${Number(sl) ? `riesgo ${ticksToText(Number(sl), symbol)}` : ""}${Number(sl) && Number(tp) ? " · " : ""}${Number(tp) ? `objetivo ${ticksToText(Number(tp), symbol)}` : ""}${Number(sl) && Number(tp) ? ` · ratio 1:${(Number(tp) / Number(sl)).toFixed(1)}` : ""} · ×${qty} contratos. Se crean como OCO al ejecutarse la entrada (también en límite y stop).` : "elige un importe rápido o escribe uno: se crean como OCO en cuanto se ejecute la entrada (también en límite y stop)"}</span>
            </div>}
            <div className={`semaforo ${check?.verdict ?? ""}`} data-testid="semaforo">
              <button className="chip-btn" disabled={checking || !account || !symbol} onClick={() => void runCheck("BUY")} data-testid="check-btn"><Icon name="shield" size={12} /> {checking ? "comprobando…" : "Semáforo"}</button>
              <label className="inline small"><input type="checkbox" checked={checkAlways} onChange={(e) => setCheckAlways(e.target.checked)} /> comprobar antes de enviar</label>
              {check && <span className={`light ${check.verdict}`} title={check.checked_at}>{check.verdict === "green" ? "VERDE" : check.verdict === "amber" ? "ÁMBAR" : "ROJO"}</span>}
              {check && (check.reasons.length ? <span className="small">{check.reasons.map((r) => r.text).join(" · ")}</span> : <span className="small ok">sin señales en contra: riesgo, racha, noticias y volatilidad en orden</span>)}
            </div>
            <div className="muted small">{last ? `último ${symbol}: ${fmtPx(last, symbol)}` : "sin precio en vivo de este instrumento"}{account === master ? ` · la orden va a la MAESTRA y se replica a ${followersOf(account)} seguidoras` : account ? " · solo esta cuenta (las seguidoras no la copian)" : ""}</div>
            {oco ? <div className="ticket-actions one"><button className="oco-btn" disabled={!canTrade || busy !== null || !symbol} onClick={() => void submitOco()} data-testid="t-oco-send">ENVIAR OCO {qty} (compra arriba / venta abajo)</button></div>
            : <div className="ticket-actions">
              <button className="buy" disabled={!canTrade || busy !== null || !symbol} onClick={() => void submit("BUY")} data-testid="t-buy">COMPRAR {qty}</button>
              <button className="sell" disabled={!canTrade || busy !== null || !symbol} onClick={() => void submit("SELL")} data-testid="t-sell">VENDER {qty}</button>
            </div>}
            {msg && <div className="small" data-testid="trade-msg">{msg}</div>}
          </div>
        </Card>
        <Card title="Enviadas en esta sesión" icon="list" right={<span className="muted small">cada orden lleva un id único: reenviar nunca duplica</span>}>
          {sent.length === 0 ? <Empty>Todavía no has enviado órdenes desde aquí.</Empty> : (
            <ul className="sent-list" data-testid="sent-list">
              {sent.map((s) => (
                <li key={s.cid} className={`sent ${s.state}`} data-testid={`sent-${s.cid}`}>
                  <div><b>{s.order.action === "BUY" ? "COMPRA" : "VENTA"} {s.order.quantity} {s.order.symbol}</b> <span className="muted">{TYPES.find((t) => t[0] === s.order.order_type)?.[1]}{s.order.limit_price ? ` @ ${s.order.limit_price}` : ""}{s.order.stop_price ? ` stop ${s.order.stop_price}` : ""} · {s.order.account} · {hhmmss(s.at)}</span></div>
                  <div className="sent-state">
                    {s.state === "sending" && <span className="badge">enviando…</span>}
                    {s.state === "accepted" && <span className="badge ok">aceptada por NinjaTrader · {s.ms} ms</span>}
                    {s.state === "filled" && <span className="badge ok">ejecutada{s.ms !== undefined ? ` · ${s.ms} ms` : ""}</span>}
                    {s.state === "unconfirmed" && <><span className="badge bad">NO CONFIRMADA</span><span className="bad small">{s.detail} · comprueba en NinjaTrader antes de repetir</span><button className="chip-btn" onClick={() => retry(s)} disabled={busy !== null}>Reenviar (mismo id)</button></>}
                    {s.state === "rejected" && <><span className="badge bad">rechazada</span><span className="bad small">{s.detail}</span></>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {symbol && (() => { const selAcc = account; const pos = positions.find((p) => p.account === selAcc && p.symbol === symbol) ?? positions.find((p) => p.symbol === symbol); const ex = pos ? exitOrders(pos.account, pos.symbol, pos.quantity) : { stops: [], targets: [] }; return (
        <Card title={`Gráfico 1 min · ${symbol}${pos ? ` · ${pos.account}` : ""}`} icon="activity" right={<span className="muted small">velas del engine con los ticks del addon · entrada, stop y objetivo de la posición{pos ? "" : " (sin posición en este instrumento)"}</span>}>
          <CandleChart client={client} symbol={symbol} account={pos?.account ?? null} quantity={pos?.quantity} avg={pos?.avg_price ?? null} stops={ex.stops} targets={ex.targets} disabled={!canClose} onMoved={(m) => setMsg(m)} />
        </Card>); })()}
      <div className="an-two">
        <Card title={`Posiciones · ${positions.length}`} icon="layers" right={<StepPicker step={step} onChange={setStep} />}>
          {positions.length === 0 ? <Empty>Sin posiciones abiertas.</Empty> : (
            <div className="table-wrap"><table className="trades" data-testid="positions">
              <thead><tr><th>Cuenta</th><th>Instrumento</th><th className="num">Posición</th><th className="num">Entrada</th><th>Stop · objetivo <span className="muted small">(▲▼ {stepLabel(step)})</span></th><th className="num">P&L</th><th></th></tr></thead>
              <tbody>{positions.map((p) => { const pnl = livePnl(p.symbol, p.quantity, p.avg_price, p.unrealized_pnl); const key = p.account + "|" + p.symbol; const exits = exitOrders(p.account, p.symbol, p.quantity); return (
                <Fragment key={key}>
                <tr className={protect === key ? "sel" : ""} data-testid={`pos-${p.account}-${root(p.symbol)}`}>
                  <td>{p.account}{p.account === master && <span className="muted small"> · maestra</span>}</td><td>{p.symbol}</td>
                  <td className={`num ${p.quantity > 0 ? "ok" : "bad"}`}>{p.quantity > 0 ? "Largo" : "Corto"} {Math.abs(p.quantity)}{(() => { const t = trailOf(p.account, p.symbol); return t ? <><br /><span className={`badge ${t.active ? "ok" : "warn"}`} data-testid={`trail-badge-${p.account}-${root(p.symbol)}`}>trailing {trailText(t)}</span></> : null; })()}</td><td className="num">{fmtPx(p.avg_price, p.symbol)}</td>
                  <td className="nowrap"><div className="nudge-cell">
                    <Nudger label="stop" o={exits.stops[0] ? asNudge({ ...exits.stops[0], account: p.account }) : null} nudge={nudge} shown={shown} disabled={!canClose} step={step} testId={`nudge-sl-${p.account}-${root(p.symbol)}`} />
                    <Nudger label="obj." o={exits.targets[0] ? asNudge({ ...exits.targets[0], account: p.account }) : null} nudge={nudge} shown={shown} disabled={!canClose} step={step} testId={`nudge-tp-${p.account}-${root(p.symbol)}`} />
                  </div></td>
                  <td className={`num ${pnl > 0 ? "ok" : pnl < 0 ? "bad" : ""}`}>{signedMoney(pnl)}</td>
                  <td className="num nowrap"><button className={`chip-btn ${protect === key ? "active" : ""}`} disabled={!canClose} onClick={() => setProtect(protect === key ? null : key)} data-testid={`protect-${p.account}-${root(p.symbol)}`}><Icon name="shield" size={12} /> Proteger</button> <button className="chip-btn danger" disabled={!canClose || busy !== null} onClick={() => closePos(p.account, p.symbol, p.quantity)} data-testid={`close-${p.account}-${root(p.symbol)}`}>Cerrar</button>{Math.abs(p.quantity) > 1 && <> <button className="chip-btn" disabled={!canClose || busy !== null} onClick={() => closePos(p.account, p.symbol, p.quantity, Math.floor(Math.abs(p.quantity) / 2))} title="Cerrar la mitad" data-testid={`half-${p.account}-${root(p.symbol)}`}>½</button> <button className="chip-btn" disabled={!canClose || busy !== null} onClick={() => closeAsk(p.account, p.symbol, p.quantity)} title="Cerrar N contratos">N…</button></>} <button className="chip-btn" disabled={busy !== null} onClick={() => void flattenAcc(p.account)}>Cerrar cuenta</button></td>
                </tr>
                </Fragment>); })}</tbody>
            </table></div>
          )}
          {(() => { const sel = positions.find((p) => p.account + "|" + p.symbol === protect); if (!sel) return null; const exits = exitOrders(sel.account, sel.symbol, sel.quantity); return (
            <ProtectPanel key={protect} client={client} account={sel.account} symbol={sel.symbol} quantity={sel.quantity} avg={sel.avg_price} last={prices[root(sel.symbol)]?.last ?? null} stops={exits.stops} targets={exits.targets}
                          isMaster={sel.account === master} followers={followersOf(sel.account)} copying={!!master && sel.account !== master && rules.some((r) => r.enabled && r.follower_account.toLowerCase() === sel.account.toLowerCase())}
                          disabled={!canClose || busy !== null} onDone={(m) => { setMsg(m); setTrailTick((x) => x + 1); }} onClose={() => setProtect(null)}
                          trail={trailOf(sel.account, sel.symbol)} aiEnabled={!!status?.ai_enabled} market={trails?.market[sel.symbol] ?? null}
                          onSwitchToMaster={master && positions.some((p) => p.account === master && p.symbol === sel.symbol) ? () => setProtect(master + "|" + sel.symbol) : undefined} />); })()}
        </Card>
        <Card title={`Órdenes vivas · ${orders.length}`} icon="clock" right={<span className="muted small">▲▼ mueven {stepLabel(step)} · pulsa una fila y usa + / − o las flechas{selOrder ? " · seleccionada" : ""}</span>}>
          {orders.length === 0 ? <Empty>Sin órdenes pendientes.</Empty> : (
            <div className="table-wrap"><table className="trades" data-testid="orders">
              <thead><tr><th>Cuenta</th><th>Orden</th><th className="num">Cant.</th><th className="num">Precio</th><th>Estado</th><th></th></tr></thead>
              <tbody>{orders.map((o) => { const price = o.order_type === "STOPMARKET" ? o.stop_price : o.limit_price || o.stop_price; const ed = edit[o.order_id]; return (
                <tr key={o.account + o.order_id} className={selOrder === o.order_id ? "sel" : ""} onClick={() => setSelOrder(o.order_id)} data-testid={`row-${o.order_id}`}>
                  <td>{o.account}{o.master_order_id ? <span className="muted small"> · copia</span> : ""}</td>
                  <td><b className={o.action.startsWith("BUY") ? "ok" : "bad"}>{o.action.startsWith("BUY") ? "Compra" : "Venta"}</b> {o.symbol} <span className="muted small">{TYPES.find((t) => t[0] === o.order_type)?.[1] ?? o.order_type}</span></td>
                  <td className="num">{o.filled ? `${o.filled}/` : ""}{o.quantity}</td>
                  <td className="num">{ed !== undefined ? <span className="px"><button className="chip-btn" onClick={() => setEdit({ ...edit, [o.order_id]: fmtPx((Number(ed) || price) - tickSize(o.symbol), o.symbol) })}>−</button><input inputMode="decimal" value={ed} onChange={(e) => setEdit({ ...edit, [o.order_id]: e.target.value })} style={{ width: 90 }} /><button className="chip-btn" onClick={() => setEdit({ ...edit, [o.order_id]: fmtPx((Number(ed) || price) + tickSize(o.symbol), o.symbol) })}>+</button></span> : <Nudger o={asNudge(o)} nudge={nudge} shown={shown} disabled={!canClose} step={step} testId={`nudge-${o.order_id}`} />}</td>
                  <td><span className="muted small">{o.state}</span></td>
                  <td className="num nowrap">
                    {ed !== undefined ? <><button className="chip-btn" disabled={busy !== null} onClick={() => void modify(o.account, o.order_id, o.order_type, Number(ed))} data-testid={`save-${o.order_id}`}>Guardar</button> <button className="chip-btn" onClick={() => setEdit((e) => { const n = { ...e }; delete n[o.order_id]; return n; })}>✕</button></>
                      : <button className="chip-btn" disabled={!canClose || busy !== null} onClick={() => setEdit({ ...edit, [o.order_id]: fmtPx(price, o.symbol) })} data-testid={`edit-${o.order_id}`}>Precio</button>}
                    {" "}<button className="chip-btn danger" disabled={!canClose || busy !== null} onClick={() => void cancel(o.account, o.order_id)} data-testid={`cancel-${o.order_id}`}>Cancelar</button>
                  </td>
                </tr>); })}</tbody>
            </table></div>
          )}
        </Card>
      </div>
      <p className="muted small">Cómo protege tu dinero: la orden solo se da por <b>aceptada</b> cuando NinjaTrader la confirma por el canal de órdenes (con un reintento automático); si no confirma verás <b>NO CONFIRMADA</b> en rojo y podrás reenviarla con el mismo id sin riesgo de duplicarla. Sin conexión en vivo, con el addon desactualizado o con el kill switch activo los botones de entrada se bloquean; cerrar y cancelar siguen disponibles siempre que haya conexión. Las órdenes en la maestra se replican a las seguidoras igual que si las pusieras en NinjaTrader; en una seguidora, solo a esa cuenta (y quedará marcada como desincronizada hasta que vuelva a coincidir con la maestra).</p>
    </div>
  );
}
