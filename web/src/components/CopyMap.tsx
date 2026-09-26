import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { useStore } from "../lib/store";
import type { Account, CopyMapLayout, ExecOptions, Rule } from "../lib/api";

/* Mapa de cuentas: tarjetas (maestras y seguidoras) unidas por líneas (reglas de copia). Arrastrar una seguidora sobre una
 * maestra crea o cambia su regla con la API existente; arrastrar una maestra sobre otra crea un grupo visual (la segunda
 * hereda la configuración de la primera; nunca se copian entre sí). Nada de lo que se hace aquí envía órdenes. */
export type View = "flow" | "cables";
type Pos = { x: number; y: number };
type Node = { id: string; account?: Account; role: "master" | "follower" | "free"; active: boolean };
type EdgeState = "active" | "paused" | "inactive";
type Edge = { id: string; rule: Rule; from: string; to: string; state: EdgeState };
type Hover = { kind: "node" | "edge" | "link"; id: string; x: number; y: number } | null;
type Drag = { id: string; offX: number; offY: number; startX: number; startY: number; moved: boolean; target: string | null; before: Pos };

export const CARD_W = 224, CARD_H = 96;
const LS_KEY = "tpx.copymap";
const EMPTY: CopyMapLayout = { view: "flow", positions: {}, links: [], show_offline: false };
const lower = (s: string) => s.trim().toLowerCase();
const same = (a: string, b: string) => lower(a) === lower(b);
const optsOf = (r?: Rule): ExecOptions => r ? { target_root: r.target_root, entry_mode: r.entry_mode, tolerance_ticks: r.tolerance_ticks, entry_timeout_s: r.entry_timeout_s, entry_fallback: r.entry_fallback } : {};

/** Colocación automática. Flujo: maestras a la izquierda, sus seguidoras a la derecha. Cables: cada maestra en el centro
 *  de un anillo con sus seguidoras, clústeres en rejilla. */
export function autoLayout(view: View, nodes: Node[], edges: Edge[], links: { a: string; b: string }[]): Record<string, Pos> {
  const pos: Record<string, Pos> = {};
  const masters = nodes.filter((n) => n.role === "master");
  const followersOf = (m: string) => edges.filter((e) => same(e.from, m) && !e.rule.symbol_filter).map((e) => e.to).filter((v, i, a) => a.findIndex((x) => same(x, v)) === i);
  // orden: maestra activa, después sus vinculadas, después el resto
  const ordered: Node[] = [];
  const push = (m: Node) => { if (!ordered.some((o) => same(o.id, m.id))) ordered.push(m); };
  for (const m of masters.filter((m) => m.active)) { push(m); for (const l of links) { const other = same(l.a, m.id) ? l.b : same(l.b, m.id) ? l.a : null; const n = other && masters.find((x) => same(x.id, other)); if (n) push(n); } }
  for (const m of masters) push(m);
  const placed = new Set<string>();
  if (view === "flow") {
    let y = 24;
    for (const m of ordered) {
      const fs = followersOf(m.id).filter((f) => !placed.has(lower(f)));
      const blockH = Math.max(1, fs.length) * (CARD_H + 18);
      pos[m.id] = { x: 24, y: y + blockH / 2 - CARD_H / 2 };
      placed.add(lower(m.id));
      fs.forEach((f, i) => { pos[f] = { x: 24 + CARD_W + 180, y: y + i * (CARD_H + 18) }; placed.add(lower(f)); });
      y += blockH + 40;
    }
    const rest = nodes.filter((n) => !placed.has(lower(n.id)));
    rest.forEach((n, i) => { pos[n.id] = { x: 24 + (CARD_W + 180) * 2, y: 24 + i * (CARD_H + 18) }; });
  } else {
    const cols = Math.max(1, Math.min(3, Math.ceil(Math.sqrt(Math.max(1, ordered.length)))));
    const cellW = CARD_W * 2 + 260, cellH = CARD_H * 2 + 300;
    ordered.forEach((m, k) => {
      const cx = 40 + (k % cols) * cellW + cellW / 2 - CARD_W / 2, cy = 40 + Math.floor(k / cols) * cellH + cellH / 2 - CARD_H / 2;
      pos[m.id] = { x: cx, y: cy }; placed.add(lower(m.id));
      const fs = followersOf(m.id).filter((f) => !placed.has(lower(f)));
      const r = Math.max(180, 60 + fs.length * 34);
      fs.forEach((f, i) => { const a = -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(1, fs.length); pos[f] = { x: Math.round(cx + Math.cos(a) * r * 1.35), y: Math.round(cy + Math.sin(a) * r * 0.8) }; placed.add(lower(f)); });
    });
    const rest = nodes.filter((n) => !placed.has(lower(n.id)));
    const baseY = 40 + Math.ceil(ordered.length / cols) * cellH + 20;
    rest.forEach((n, i) => { pos[n.id] = { x: 40 + (i % 4) * (CARD_W + 30), y: baseY + Math.floor(i / 4) * (CARD_H + 24) }; });
  }
  for (const k of Object.keys(pos)) pos[k] = { x: Math.max(0, Math.round(pos[k].x)), y: Math.max(0, Math.round(pos[k].y)) };
  return pos;
}

function edgePath(view: View, a: Pos, b: Pos): string {
  const x1 = a.x + CARD_W, y1 = a.y + CARD_H / 2, x2 = b.x, y2 = b.y + CARD_H / 2;
  if (view === "flow") { const dx = Math.max(40, Math.abs(x2 - x1) / 2); return `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`; }
  // cables: ortogonal desde el centro de la maestra al centro de la seguidora, saliendo por el lado más cercano
  const acx = a.x + CARD_W / 2, acy = a.y + CARD_H / 2, bcx = b.x + CARD_W / 2, bcy = b.y + CARD_H / 2;
  if (Math.abs(bcx - acx) > Math.abs(bcy - acy)) { const sx = bcx > acx ? a.x + CARD_W : a.x, ex = bcx > acx ? b.x : b.x + CARD_W; const mx = (sx + ex) / 2; return `M${sx},${acy} H${mx} V${bcy} H${ex}`; }
  const sy = bcy > acy ? a.y + CARD_H : a.y, ey = bcy > acy ? b.y : b.y + CARD_H; const my = (sy + ey) / 2; return `M${acx},${sy} V${my} H${bcx} V${ey}`;
}
function linkPath(view: View, a: Pos, b: Pos): string {
  const acx = a.x + CARD_W / 2, acy = a.y + CARD_H / 2, bcx = b.x + CARD_W / 2, bcy = b.y + CARD_H / 2;
  if (view === "flow") return `M${acx},${a.y + CARD_H} C${acx},${(acy + bcy) / 2} ${bcx},${(acy + bcy) / 2} ${bcx},${b.y}`;
  const my = (acy + bcy) / 2; return `M${acx},${acy} V${my} H${bcx} V${bcy}`;
}

export function CopyMap() {
  const { client, accounts, rules, setRules, health, risk, setRisk } = useStore();
  const master = health?.bridge.master_account ?? null;
  const [layout, setLayoutState] = useState<CopyMapLayout | null>(null);
  const layoutRef = useRef<CopyMapLayout>(EMPTY);          // siempre el último estado: las acciones asíncronas no deben usar uno viejo
  const setLayout = useCallback((l: CopyMapLayout) => { layoutRef.current = l; setLayoutState(l); }, []);
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<Hover>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "bad" | "info"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const canvas = useRef<HTMLDivElement>(null);
  const saveTimer = useRef<number | null>(null);
  const view: View = layout?.view ?? "flow";

  // ---- carga y guardado de la disposición (engine, con copia en el navegador) ----
  useEffect(() => {
    if (!client) return;
    let alive = true;
    client.copyMap().then((l) => { if (alive) setLayout(l); try { localStorage.setItem(LS_KEY, JSON.stringify(l)); } catch { /* privado */ } })
      .catch(() => { try { const raw = localStorage.getItem(LS_KEY); if (alive) setLayout(raw ? JSON.parse(raw) : { ...EMPTY }); } catch { if (alive) setLayout({ ...EMPTY }); } });
    return () => { alive = false; };
  }, [client, setLayout]);
  const persist = useCallback((next: CopyMapLayout) => {
    setLayout(next);
    try { localStorage.setItem(LS_KEY, JSON.stringify(next)); } catch { /* privado */ }
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => { client?.saveCopyMap(next).catch(() => setToast({ kind: "bad", text: "No se pudo guardar la disposición en el engine (queda en este navegador)." })); }, 500);
  }, [client, setLayout]);
  const current = () => layoutRef.current;
  useEffect(() => { if (!toast) return; const t = window.setTimeout(() => setToast(null), 4500); return () => window.clearTimeout(t); }, [toast]);

  // ---- nodos y aristas a partir de cuentas y reglas reales ----
  const canon = useCallback((id: string) => accounts.find((a) => same(a.account_id, id))?.account_id ?? id.trim(), [accounts]);
  const masterIds = useMemo(() => { const s: string[] = []; const add = (x: string) => { if (x && !s.some((y) => same(y, x))) s.push(canon(x)); }; if (master) add(master); for (const r of rules) add(r.master_account); return s; }, [master, rules, canon]);
  const nodes: Node[] = useMemo(() => {
    const out: Node[] = [];
    const add = (id: string, account?: Account) => { if (!out.some((n) => same(n.id, id))) out.push({ id, account, role: masterIds.some((m) => same(m, id)) ? "master" : rules.some((r) => same(r.follower_account, id)) ? "follower" : "free", active: !!master && same(id, master) }); };
    for (const a of accounts) if ((a.enabled && a.reported) || layout?.show_offline || masterIds.some((m) => same(m, a.account_id)) || rules.some((r) => same(r.follower_account, a.account_id))) add(a.account_id, a);
    for (const r of rules) { add(canon(r.master_account), accounts.find((a) => same(a.account_id, r.master_account))); add(canon(r.follower_account), accounts.find((a) => same(a.account_id, r.follower_account))); }
    return out;
  }, [accounts, rules, masterIds, master, layout?.show_offline, canon]);
  const edges: Edge[] = useMemo(() => rules.map((r) => ({ id: r.id, rule: r, from: canon(r.master_account), to: canon(r.follower_account),
    state: !r.enabled ? "paused" : master && same(r.master_account, master) ? "active" : "inactive" })), [rules, canon, master]);
  const links = useMemo(() => (layout?.links ?? []).filter((l) => nodes.some((n) => same(n.id, l.a)) && nodes.some((n) => same(n.id, l.b))), [layout, nodes]);

  const positions: Record<string, Pos> = useMemo(() => {
    const saved = layout?.positions?.[view] ?? {};
    const auto = autoLayout(view, nodes, edges, links);
    const out: Record<string, Pos> = {};
    for (const n of nodes) { const s = saved[n.id] ?? Object.entries(saved).find(([k]) => same(k, n.id))?.[1]; out[n.id] = s ? { x: s.x, y: s.y } : auto[n.id] ?? { x: 24, y: 24 }; }
    return out;
  }, [layout, view, nodes, edges, links]);
  // al abrir, dejar a la vista la maestra activa (en el móvil el lienzo es más ancho que la pantalla)
  const scrolled = useRef(false);
  useEffect(() => {
    if (scrolled.current || !layout || !canvas.current || !master) return;
    const p = positions[nodes.find((n) => same(n.id, master))?.id ?? ""];
    if (!p) return;
    scrolled.current = true;
    canvas.current.scrollTo({ left: Math.max(0, p.x - 24), top: Math.max(0, p.y - 24) });
  }, [layout, positions, nodes, master]);
  const size = useMemo(() => { let w = 600, h = 360; for (const p of Object.values(positions)) { w = Math.max(w, p.x + CARD_W + 40); h = Math.max(h, p.y + CARD_H + 40); } return { w, h }; }, [positions]);

  const setPos = useCallback((id: string, p: Pos, save = true) => {
    const base = layoutRef.current;
    const next: CopyMapLayout = { ...base, positions: { ...base.positions, [view]: { ...(base.positions[view] ?? Object.fromEntries(Object.entries(positions))), [id]: { x: Math.max(0, Math.round(p.x)), y: Math.max(0, Math.round(p.y)) } } } };
    if (save) persist(next); else setLayout(next);
  }, [view, positions, persist, setLayout]);
  const setView = (v: View) => persist({ ...current(), view: v });
  const reorder = () => { const base = current(); persist({ ...base, positions: { ...base.positions, [view]: autoLayout(view, nodes, edges, links) } }); setToast({ kind: "info", text: "Mapa reordenado." }); };

  // ---- acciones sobre reglas (la API existente; nunca órdenes) ----
  const plainRuleOf = (follower: string) => rules.find((r) => same(r.follower_account, follower) && !r.symbol_filter);
  const templateOf = (m: string) => rules.find((r) => same(r.master_account, m) && !r.symbol_filter && r.enabled) ?? rules.find((r) => same(r.master_account, m) && !r.symbol_filter);
  async function refreshRules() { if (client) try { setRules(await client.rules()); } catch { /* se queda lo que hay */ } }
  async function linkFollower(follower: string, to: string, extra: { multiplier?: number; enabled?: boolean } & ExecOptions = {}) {
    if (!client || busy) return false;
    const prev = plainRuleOf(follower);
    setBusy(true);
    try {
      const r = await client.link(follower, to, extra.multiplier ?? prev?.multiplier ?? 1, extra.enabled ?? prev?.enabled ?? true, { ...optsOf(prev), ...extra });
      let next = rules.filter((x) => x.id !== r.id).concat(r);
      if (prev && !same(prev.master_account, to)) { await client.unlink(follower, prev.master_account); next = next.filter((x) => x.id !== prev.id); }
      setRules(next);
      setToast({ kind: "ok", text: `${follower} copia a ${to} ×${r.multiplier}${r.enabled ? "" : " (pausada)"}.` });
      return true;
    } catch (ex) {
      setToast({ kind: "bad", text: `No se pudo vincular ${follower} con ${to}: ${ex instanceof Error ? ex.message : String(ex)}` });
      await refreshRules();
      return false;
    } finally { setBusy(false); }
  }
  async function unlinkFollower(follower: string) {
    const prev = plainRuleOf(follower);
    if (!client || !prev || !confirm(`¿Desconectar ${follower} de ${prev.master_account}? Dejará de copiar. Las posiciones abiertas no se tocan.`)) return;
    setBusy(true);
    try { await client.unlink(follower, prev.master_account); setRules(rules.filter((x) => x.id !== prev.id)); setToast({ kind: "ok", text: `${follower} desconectada.` }); }
    catch (ex) { setToast({ kind: "bad", text: `No se pudo desconectar: ${ex instanceof Error ? ex.message : String(ex)}` }); await refreshRules(); }
    finally { setBusy(false); }
  }
  const linked = (a: string, b: string) => links.some((l) => (same(l.a, a) && same(l.b, b)) || (same(l.a, b) && same(l.b, a)));
  async function groupMasters(a: string, b: string) {
    if (!client || same(a, b)) return;
    if (linked(a, b)) { setToast({ kind: "info", text: `${a} y ${b} ya están vinculadas.` }); return; }
    const tpl = templateOf(a);
    const targets = rules.filter((r) => same(r.master_account, b) && !r.symbol_filter);
    const desc = tpl ? `Las ${targets.length} seguidoras de ${b} heredan la configuración de ${a}: ×${tpl.multiplier}, entrada ${tpl.entry_mode === "limit" ? `límite ±${tpl.tolerance_ticks} ticks` : "a mercado"}${tpl.target_root ? `, símbolo ${tpl.target_root}` : ""}.` : `${a} no tiene seguidoras: no hay configuración que heredar.`;
    if (!confirm(`¿Vincular ${b} con ${a} como grupo?\n\n${desc}\n\n${a} y ${b} NO se copian entre sí; es un grupo visual con la misma configuración.`)) return;
    setBusy(true);
    try {
      let next = [...rules];
      if (tpl) for (const r of targets) { const u = await client.updateRule(r.id, { multiplier: tpl.multiplier, entry_mode: tpl.entry_mode, tolerance_ticks: tpl.tolerance_ticks, entry_timeout_s: tpl.entry_timeout_s, entry_fallback: tpl.entry_fallback, target_root: tpl.target_root }); next = next.map((x) => (x.id === u.id ? u : x)); }
      setRules(next);
      persist({ ...current(), links: [...current().links, { a, b }] });
      setToast({ kind: "ok", text: `${b} vinculada con ${a}${tpl ? ` (${targets.length} seguidoras con la configuración de ${a})` : ""}.` });
    } catch (ex) { setToast({ kind: "bad", text: `No se pudo vincular: ${ex instanceof Error ? ex.message : String(ex)}` }); await refreshRules(); }
    finally { setBusy(false); }
  }
  const ungroup = (a: string, b: string) => { persist({ ...current(), links: current().links.filter((l) => !((same(l.a, a) && same(l.b, b)) || (same(l.a, b) && same(l.b, a)))) }); setToast({ kind: "ok", text: `${a} y ${b} desvinculadas.` }); };
  async function toggleCopy() {
    if (!client) return;
    const active = !risk?.kill_switch;
    if (active && !confirm("¿Pausar toda la copia? Se bloquean las réplicas nuevas; las posiciones y órdenes abiertas se quedan como están.")) return;
    try { setRisk(await client.killSwitch(active, active ? "pausa desde el mapa" : undefined, false, false)); } catch (ex) { setToast({ kind: "bad", text: ex instanceof Error ? ex.message : String(ex) }); }
  }

  // ---- arrastre ----
  const nodeById = (id: string) => nodes.find((n) => same(n.id, id));
  const dropTarget = (id: string, px: number, py: number): string | null => {
    const me = nodeById(id); if (!me) return null;
    for (const n of nodes) {
      if (same(n.id, id) || n.role !== "master") continue;
      const p = positions[n.id]; if (!p) continue;
      if (px >= p.x && px <= p.x + CARD_W && py >= p.y && py <= p.y + CARD_H) return n.id;
    }
    return null;
  };
  const toCanvas = (e: { clientX: number; clientY: number }) => { const r = canvas.current!.getBoundingClientRect(); return { x: e.clientX - r.left + canvas.current!.scrollLeft, y: e.clientY - r.top + canvas.current!.scrollTop }; };
  const onDown = (id: string) => (e: ReactPointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button, select, input, a")) return;
    const c = toCanvas(e); const p = positions[id];
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ id, offX: c.x - p.x, offY: c.y - p.y, startX: c.x, startY: c.y, moved: false, target: null, before: { ...p } });
    setHover(null);
  };
  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const c = toCanvas(e);
    const moved = drag.moved || Math.hypot(c.x - drag.startX, c.y - drag.startY) > 4;
    if (!moved) return;
    const p = { x: c.x - drag.offX, y: c.y - drag.offY };
    setPos(drag.id, p, false);
    setDrag({ ...drag, moved: true, target: dropTarget(drag.id, c.x, c.y) });
  };
  const onUp = async (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const d = drag; setDrag(null);
    const me = nodeById(d.id);
    if (!d.moved) {  // clic o toque: seleccionar (y en táctil, mostrar el pop-up)
      setSelected(selected === d.id ? null : d.id);
      if (e.pointerType === "touch") { const c = toCanvas(e); setHover({ kind: "node", id: d.id, x: c.x, y: c.y }); }
      return;
    }
    if (d.target && me) {
      // volver a la posición anterior: la tarjeta no se queda encima de la maestra
      setPos(d.id, d.before, true);
      if (me.role === "master") await groupMasters(d.target, d.id);
      else { const ok = await linkFollower(d.id, d.target); if (!ok) setPos(d.id, d.before, true); }
      return;
    }
    setPos(d.id, positions[d.id], true);
  };

  // ---- teclado ----
  const onKey = (id: string) => (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 40 : 10; const p = positions[id];
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(selected === id ? null : id); }
    else if (e.key === "Escape") setSelected(null);
    else if (e.key === "ArrowLeft") { e.preventDefault(); setPos(id, { x: p.x - step, y: p.y }); }
    else if (e.key === "ArrowRight") { e.preventDefault(); setPos(id, { x: p.x + step, y: p.y }); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setPos(id, { x: p.x, y: p.y - step }); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setPos(id, { x: p.x, y: p.y + step }); }
    else if ((e.key === "Delete" || e.key === "Backspace") && nodeById(id)?.role === "follower") { e.preventDefault(); void unlinkFollower(id); }
  };

  // ---- pop-up ----
  const label = (id: string) => nodeById(id)?.account?.alias || id;
  const stateText: Record<EdgeState, string> = { active: "copiando", paused: "pausada", inactive: "inactiva (maestra no activa)" };
  const hoverContent = () => {
    if (!hover) return null;
    if (hover.kind === "edge") { const e = edges.find((x) => x.id === hover.id); if (!e) return null; return (
      <><b>{label(e.from)} → {label(e.to)}</b><div>×{e.rule.multiplier} · {e.rule.symbol_filter ?? "todos los símbolos"}{e.rule.target_root ? ` → ${e.rule.target_root}` : ""}</div><div className={e.state === "active" ? "ok" : e.state === "paused" ? "muted" : "warn"}>{stateText[e.state]} · entrada {e.rule.entry_mode === "limit" ? `límite ±${e.rule.tolerance_ticks}` : "a mercado"}</div></>); }
    if (hover.kind === "link") { const [a, b] = hover.id.split("|"); return <><b>{label(a)} ⇄ {label(b)}</b><div className="muted">grupo de maestras: misma configuración, no se copian entre sí</div></>; }
    const n = nodeById(hover.id); if (!n) return null;
    const out = edges.filter((e) => same(e.from, n.id)), inn = edges.filter((e) => same(e.to, n.id));
    const lk = links.filter((l) => same(l.a, n.id) || same(l.b, n.id)).map((l) => (same(l.a, n.id) ? l.b : l.a));
    return (
      <><b>{label(n.id)}</b> <span className="muted">{n.role === "master" ? (n.active ? "maestra activa" : "maestra") : n.role === "follower" ? "seguidora" : "sin vincular"}</span>
        {n.account && <div className="muted">{n.account.connected === false ? "desconectada" : n.account.connected ? "conectada" : n.account.connection || "sin estado"}{n.account.open_positions.length ? ` · ${n.account.open_positions.length} posición(es)` : " · plana"}</div>}
        {out.map((e) => <div key={e.id}>→ {label(e.to)} ×{e.rule.multiplier} {e.rule.symbol_filter ? `· ${e.rule.symbol_filter}` : ""}<span className={e.state === "active" ? "ok" : e.state === "paused" ? "muted" : "warn"}> · {stateText[e.state]}</span></div>)}
        {inn.map((e) => <div key={e.id}>← {label(e.from)} ×{e.rule.multiplier} {e.rule.symbol_filter ? `· ${e.rule.symbol_filter}` : ""}{e.rule.target_root ? ` → ${e.rule.target_root}` : ""}<span className={e.state === "active" ? "ok" : e.state === "paused" ? "muted" : "warn"}> · {stateText[e.state]}</span></div>)}
        {lk.map((m) => <div key={m} className="accent">⇄ {label(m)} <span className="muted">· grupo</span></div>)}
        {!out.length && !inn.length && !lk.length && <div className="muted">sin conexiones</div>}</>
    );
  };
  const hoverNode = (id: string) => (e: ReactPointerEvent<HTMLElement>) => { if (e.pointerType === "touch" || drag) return; const c = toCanvas(e); setHover({ kind: "node", id, x: c.x, y: c.y }); };
  const hoverEdge = (kind: "edge" | "link", id: string) => (e: ReactPointerEvent<SVGElement>) => { if (e.pointerType === "touch") return; const c = toCanvas(e); setHover({ kind, id, x: c.x, y: c.y }); };

  const sel = selected ? nodeById(selected) : null;
  const desyncs = accounts.filter((a) => a.desync).length;
  const followerCount = new Set(edges.filter((e) => e.state === "active").map((e) => lower(e.to))).size;

  return (
    <div className="copymap" data-testid="copymap">
      <div className="copymap-bar">
        <div className="chips">
          <div className="view-tabs" data-testid="map-view"><button className={view === "flow" ? "active" : ""} onClick={() => setView("flow")}>Flujo</button><button className={view === "cables" ? "active" : ""} onClick={() => setView("cables")}>Cables</button></div>
          <button className="chip-btn" onClick={reorder} title="Colocación automática para esta vista">Reordenar</button>
          <label className="inline small"><input type="checkbox" checked={!!layout?.show_offline} onChange={(e) => persist({ ...current(), show_offline: e.target.checked })} /> ver desconectadas</label>
        </div>
        <div className="chips">
          <span className={`badge ${health?.bridge.connected ? "ok" : "bad"}`}>{health?.mode === "mock" ? "Simulador" : `NinjaTrader${health?.bridge.addon_version ? ` · addon v${health.bridge.addon_version}` : ""}`}{health?.addon_outdated ? " · desactualizado" : ""}</span>
          {risk?.addon_silent && <span className="badge bad">sin heartbeat</span>}
          {desyncs > 0 && <span className="badge bad">{desyncs} desincronizada{desyncs > 1 ? "s" : ""}</span>}
          <span className="badge muted">{followerCount} copiando</span>
          <button className={risk?.kill_switch ? "primary small-btn" : "danger small-btn"} onClick={() => void toggleCopy()} data-testid="map-kill">{risk?.kill_switch ? "Reanudar copia" : "Pausar copia"}</button>
        </div>
      </div>
      {risk?.kill_switch && <div className="banner bad">Copia detenida (kill switch){risk.kill_switch_reason ? `: ${risk.kill_switch_reason}` : ""}. Ninguna regla replica hasta reanudar.</div>}

      <div className={`copymap-body ${sel ? "has-panel" : ""}`}>
        <div className={`copymap-canvas ${view}`} ref={canvas} onPointerMove={onMove} onPointerUp={(e) => void onUp(e)} onPointerCancel={(e) => void onUp(e)} onPointerLeave={(e) => { if (e.pointerType !== "touch" && !drag) setHover(null); }}
             onPointerDown={(e) => { if ((e.target as HTMLElement).closest(".map-card, .map-tip, svg")) return; setHover(null); }}
             style={{ minHeight: 420 }} data-testid="map-canvas">
          <div className="copymap-inner" style={{ width: size.w, height: size.h }}>
            <svg className="copymap-edges" width={size.w} height={size.h} aria-hidden="false" role="img" aria-label="Conexiones de copia">
              <defs>
                {(["active", "paused", "inactive"] as EdgeState[]).map((s) => <marker key={s} id={`arrow-${s}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" className={`arrow ${s}`} /></marker>)}
              </defs>
              {links.map((l) => { const a = positions[canon(l.a)], b = positions[canon(l.b)]; if (!a || !b) return null; const id = `${l.a}|${l.b}`; return (
                <g key={id} className={`link ${hover?.kind === "link" && hover.id === id ? "hot" : ""}`} onPointerMove={hoverEdge("link", id)} onPointerLeave={(e) => { if (e.pointerType !== "touch") setHover(null); }} onClick={() => setHover((h) => (h?.kind === "link" && h.id === id ? null : { kind: "link", id, x: (a.x + b.x) / 2 + CARD_W / 2, y: (a.y + b.y) / 2 }))}>
                  <path d={linkPath(view, a, b)} className="hit" /><path d={linkPath(view, a, b)} className="line" /></g>); })}
              {edges.map((e) => { const a = positions[e.from], b = positions[e.to]; if (!a || !b) return null; const hot = hover?.kind === "edge" && hover.id === e.id || (selected && (same(selected, e.from) || same(selected, e.to))); return (
                <g key={e.id} className={`edge ${e.state} ${hot ? "hot" : ""} ${e.rule.symbol_filter ? "filtered" : ""}`} onPointerMove={hoverEdge("edge", e.id)} onPointerLeave={(e) => { if (e.pointerType !== "touch") setHover(null); }} onClick={() => setHover((h) => (h?.kind === "edge" && h.id === e.id ? null : { kind: "edge", id: e.id, x: (a.x + b.x) / 2 + CARD_W / 2, y: (a.y + b.y) / 2 + CARD_H / 2 }))} data-testid={`edge-${e.rule.follower_account}`}>
                  <path d={edgePath(view, a, b)} className="hit" /><path d={edgePath(view, a, b)} className="line" markerEnd={`url(#arrow-${e.state})`} />
                  <text x={(a.x + b.x) / 2 + CARD_W / 2} y={(a.y + b.y) / 2 + CARD_H / 2 - 6} textAnchor="middle" className="edge-label">×{e.rule.multiplier}{e.rule.symbol_filter ? ` ${e.rule.symbol_filter}` : ""}{e.rule.target_root ? ` → ${e.rule.target_root}` : ""}{e.state === "paused" ? " · pausada" : e.state === "inactive" ? " · inactiva" : ""}</text></g>); })}
            </svg>
            {nodes.map((n) => { const p = positions[n.id]; const isTarget = drag?.target && same(drag.target, n.id); const dragging = drag && same(drag.id, n.id); const inn = edges.filter((e) => same(e.to, n.id) && !e.rule.symbol_filter)[0];
              const a = n.account; const conn = !a ? "sin datos" : a.connected === false ? "desconectada" : a.connected ? "conectada" : a.connection || "sin estado"; return (
              <div key={n.id} className={`map-card ${n.role} ${n.active ? "active" : ""} ${selected && same(selected, n.id) ? "selected" : ""} ${isTarget ? "target" : ""} ${dragging ? "dragging" : ""} ${a && a.connected === false ? "offline" : ""} ${inn && !inn.rule.enabled ? "paused" : ""}`}
                   style={{ left: p.x, top: p.y, width: CARD_W, height: CARD_H }} tabIndex={0} role="button" aria-label={`${label(n.id)}, ${n.role === "master" ? "maestra" : "seguidora"}`}
                   data-account={n.id} data-role={n.role} onPointerDown={onDown(n.id)} onPointerMove={hoverNode(n.id)} onPointerLeave={(e) => { if (e.pointerType !== "touch" && !drag) setHover(null); }} onKeyDown={onKey(n.id)}>
                <div className="mc-head"><span className={`mc-role ${n.role}`}>{n.role === "master" ? (n.active ? "MAESTRA ACTIVA" : "MAESTRA") : n.role === "follower" ? "SEGUIDORA" : "SIN VINCULAR"}</span><span className={`dot ${!a ? "muted" : a.connected === false ? "bad" : a.desync ? "warn" : "ok"}`} title={conn} /></div>
                <div className="mc-name" title={n.id}>{label(n.id)}</div>
                <div className="mc-meta">
                  {n.role === "follower" && inn && <span className={`chip ${inn.rule.enabled ? "" : "bad"}`}>×{inn.rule.multiplier}{inn.rule.target_root ? ` → ${inn.rule.target_root}` : ""}{inn.rule.enabled ? "" : " · pausada"}</span>}
                  {n.role === "master" && <span className="chip">{edges.filter((e) => same(e.from, n.id)).length} seguidoras</span>}
                  {a?.desync && <span className="chip bad">desinc.</span>}
                  <span className="muted small">{conn}</span>
                </div>
                {isTarget && <div className="mc-drop">{nodeById(drag!.id)?.role === "master" ? `Soltar: vincular con ${label(n.id)}` : `Soltar: copiar a ${label(n.id)}`}</div>}
              </div>); })}
            {hover && <div className="map-tip" style={{ left: Math.min(hover.x + 14, size.w - 260), top: hover.y + 14 }} role="tooltip">{hoverContent()}</div>}
          </div>
        </div>

        {sel && (
          <aside className="map-panel" data-testid="map-panel">
            <div className="card-head"><h2>{label(sel.id)}</h2><button className="chip-btn" onClick={() => setSelected(null)}>Cerrar</button></div>
            {sel.role === "master" ? <MasterPanel n={sel} nodes={nodes} edges={edges} links={links} label={label} masters={masterIds} busy={busy} onGroup={(b) => void groupMasters(sel.id, b)} onUngroup={(b) => ungroup(sel.id, b)}
                                                 onActivate={async () => { if (!client || !confirm(`¿Hacer ${sel.id} la maestra activa en NinjaTrader? Las reglas de la maestra anterior quedan inactivas.`)) return; try { await client.setMaster(sel.id); setToast({ kind: "ok", text: `${sel.id} es ahora la maestra activa.` }); } catch (ex) { setToast({ kind: "bad", text: ex instanceof Error ? ex.message : String(ex) }); } }}
                                                 onSelect={setSelected} />
                                  : <FollowerPanel n={sel} rule={plainRuleOf(sel.id)} masters={masterIds} label={label} busy={busy} onLink={(to, extra) => void linkFollower(sel.id, to, extra)} onUnlink={() => void unlinkFollower(sel.id)} />}
          </aside>
        )}
      </div>

      <div className="copymap-legend">
        <span><i className="lg active" />copiando</span><span><i className="lg paused" />pausada</span><span><i className="lg inactive" />inactiva (otra maestra)</span><span><i className="lg group" />grupo de maestras</span>
        <span className="muted">· arrastra una seguidora sobre una maestra para cambiar de maestra; una maestra sobre otra para agruparlas · teclado: Enter abre, flechas mueven, Supr desconecta</span>
      </div>
      {toast && <div className={`map-toast ${toast.kind}`} role="status" data-testid="map-toast">{toast.text}</div>}
    </div>
  );
}

function FollowerPanel({ n, rule, masters, label, busy, onLink, onUnlink }: { n: Node; rule?: Rule; masters: string[]; label: (id: string) => string; busy: boolean;
  onLink: (to: string, extra: { multiplier?: number; enabled?: boolean } & ExecOptions) => void; onUnlink: () => void }) {
  const [to, setTo] = useState(rule?.master_account ?? masters[0] ?? "");
  const [mult, setMult] = useState(String(rule?.multiplier ?? 1));
  const [root, setRoot] = useState(rule?.target_root ?? "");
  const [mode, setMode] = useState<"market" | "limit">(rule?.entry_mode ?? "market");
  // solo se reinicia el formulario cuando cambia la regla o la cuenta, no con cada refresco de cuentas (borraba lo escrito)
  const key = `${n.id}|${rule?.id ?? ""}|${rule?.master_account ?? ""}|${rule?.multiplier ?? ""}|${rule?.target_root ?? ""}|${rule?.entry_mode ?? ""}|${masters.join(",")}`;
  useEffect(() => { setTo(rule?.master_account ?? masters[0] ?? ""); setMult(String(rule?.multiplier ?? 1)); setRoot(rule?.target_root ?? ""); setMode(rule?.entry_mode ?? "market"); }, [key]);  // eslint-disable-line react-hooks/exhaustive-deps
  const apply = (extra: { enabled?: boolean } = {}) => onLink(to, { multiplier: Number(mult) > 0 ? Number(mult) : 1, target_root: root.trim() || null, entry_mode: mode, ...extra });
  return (
    <div className="map-form">
      <p className="muted small">{rule ? <>Copia a <b>{label(rule.master_account)}</b>{rule.enabled ? "" : " (pausada)"}.</> : "Sin regla de copia. Elige una maestra y guarda."}</p>
      <label>Maestra<select value={to} onChange={(e) => setTo(e.target.value)} data-testid="panel-master">{masters.map((m) => <option key={m} value={m}>{label(m)}</option>)}</select></label>
      <label>Multiplicador<input type="number" step="0.1" min="0.1" value={mult} onChange={(e) => setMult(e.target.value)} data-testid="panel-mult" /></label>
      <label>Símbolo destino<input value={root} placeholder="igual que la maestra · ej. MNQ" onChange={(e) => setRoot(e.target.value.toUpperCase())} /></label>
      <label>Entrada<select value={mode} onChange={(e) => setMode(e.target.value as "market" | "limit")}><option value="market">A mercado</option><option value="limit">Límite al precio del maestro</option></select></label>
      <div className="chips">
        <button className="primary small-btn" disabled={busy || !to} onClick={() => apply()} data-testid="panel-save">{rule ? "Guardar" : "Vincular"}</button>
        {rule && <button className="small-btn" disabled={busy} onClick={() => apply({ enabled: !rule.enabled })}>{rule.enabled ? "Pausar" : "Reanudar"}</button>}
        {rule && <button className="danger small-btn" disabled={busy} onClick={onUnlink} data-testid="panel-unlink">Desconectar</button>}
      </div>
    </div>
  );
}

function MasterPanel({ n, nodes, edges, links, label, masters, busy, onGroup, onUngroup, onActivate, onSelect }: { n: Node; nodes: Node[]; edges: Edge[]; links: { a: string; b: string }[]; label: (id: string) => string; masters: string[]; busy: boolean;
  onGroup: (b: string) => void; onUngroup: (b: string) => void; onActivate: () => void; onSelect: (id: string) => void }) {
  const out = edges.filter((e) => same(e.from, n.id));
  const mine = links.filter((l) => same(l.a, n.id) || same(l.b, n.id)).map((l) => (same(l.a, n.id) ? l.b : l.a));
  const others = masters.filter((m) => !same(m, n.id) && !mine.some((x) => same(x, m)));
  const [pick, setPick] = useState("");
  const stateText: Record<EdgeState, string> = { active: "copiando", paused: "pausada", inactive: "inactiva" };
  return (
    <div className="map-form">
      <p className="muted small">{n.active ? "Maestra activa: sus operaciones se copian." : "Maestra no activa: sus reglas no replican hasta activarla."}</p>
      {!n.active && <button className="small-btn" onClick={onActivate} disabled={busy}>Activar como maestra</button>}
      <h3 className="small muted">Seguidoras · {out.length}</h3>
      {out.length === 0 ? <p className="muted small">Ninguna. Arrastra una seguidora sobre esta tarjeta.</p> : (
        <ul className="map-list">{out.map((e) => <li key={e.id}><button className="link" onClick={() => onSelect(e.to)}>{label(e.to)}</button><span className="chip">×{e.rule.multiplier}{e.rule.symbol_filter ? ` · ${e.rule.symbol_filter}` : ""}</span><span className={`small ${e.state === "active" ? "ok" : e.state === "paused" ? "muted" : "warn"}`}>{stateText[e.state]}</span></li>)}</ul>
      )}
      <h3 className="small muted">Maestras vinculadas · {mine.length}</h3>
      {mine.length === 0 ? <p className="muted small">Ninguna. Un grupo comparte configuración; no se copian entre sí.</p> : (
        <ul className="map-list">{mine.map((m) => <li key={m}><button className="link" onClick={() => onSelect(m)}>{label(m)}</button><span className="chip">grupo</span><button className="ghost small-btn" disabled={busy} onClick={() => onUngroup(m)} data-testid={`ungroup-${m}`}>Desvincular</button></li>)}</ul>
      )}
      {others.length > 0 && (
        <div className="fee-row"><select value={pick} onChange={(e) => setPick(e.target.value)} data-testid="panel-group-pick"><option value="">Vincular con…</option>{others.map((m) => <option key={m} value={m}>{label(m)}</option>)}</select>
          <button className="small-btn" disabled={busy || !pick} onClick={() => { onGroup(pick); setPick(""); }} data-testid="panel-group">Vincular</button></div>
      )}
      <p className="muted small">{nodes.filter((x) => x.role === "free").length} cuentas sin vincular en el mapa.</p>
    </div>
  );
}
