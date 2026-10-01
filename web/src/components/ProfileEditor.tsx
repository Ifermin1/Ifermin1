import { useEffect, useState } from "react";
import type { Account, RiskLimit } from "../lib/api";
import { DD_LABEL, FIRMS, LOCK_LABEL, floorCap, firmOf, planOf, sizeLabel, type DdType, type Lock } from "../lib/propfirms";
import { money } from "../lib/format";

/* Editor del perfil de prop firm de una cuenta: firma → plan → tamaño rellenan los límites (drawdown y su tipo, suelo,
 * objetivo, pérdida diaria, contratos); todo es editable antes de aplicar. Al aplicar se guarda el perfil en la cuenta y
 * los límites en Riesgo (el engine vigila el drawdown con ese tipo desde ese momento). */
export type ProfileResult = { firm: string; plan: string; size: number; dd: DdType; drawdown: number; cap: number; target: number; dayCut: number; dailyLoss: number; contracts: number; buffer: number };

export function ProfileEditor({ a, limit, followers, onApply, onClose, busy }: {
  a: Account; limit?: RiskLimit; followers: Account[]; busy?: boolean; onClose: () => void;
  onApply: (r: ProfileResult, alsoTo: string[]) => Promise<void>;
}) {
  const [firm, setFirm] = useState(a.firm || "apex");
  const [plan, setPlan] = useState(a.plan || (firmOf(a.firm || "apex")?.plans[0].id ?? ""));
  const [size, setSize] = useState<number>(a.plan_size || 0);
  const [dd, setDd] = useState<DdType>(limit?.drawdown_mode === "static" ? "static" : limit?.drawdown_mode === "eod" ? "eod" : "intraday");
  const [lock, setLock] = useState<Lock>("none");
  const [drawdown, setDrawdown] = useState(String(limit?.max_trailing_drawdown || ""));
  const [target, setTarget] = useState(String(limit?.profit_goal || ""));
  const [dayCut, setDayCut] = useState(String(limit?.max_daily_profit || ""));
  const [dailyLoss, setDailyLoss] = useState(String(limit?.max_daily_loss || ""));
  const [contracts, setContracts] = useState(String(limit?.max_position_size || ""));
  const [buffer, setBuffer] = useState(String(limit?.drawdown_buffer || "100"));
  const [others, setOthers] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const f = firmOf(firm); const p = planOf(firm, plan);

  // al elegir firma/plan/tamaño se rellenan los importes del catálogo (luego se pueden retocar)
  const fill = (fi: string, pl: string, sizeN: number) => {
    const pp = planOf(fi, pl); if (!pp) return;
    const s = pp.sizes.find((x) => x.size === sizeN) ?? pp.sizes[0];
    setSize(s.size); setDd(pp.dd); setLock(pp.lock); setDrawdown(String(s.drawdown)); setTarget(s.target ? String(s.target) : ""); setDailyLoss(s.dailyLoss ? String(s.dailyLoss) : ""); setContracts(String(s.contracts));
  };
  useEffect(() => { if (!a.firm) fill(firm, plan, size); /* cuenta sin perfil: valores del catálogo */ }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const changeFirm = (id: string) => { const first = firmOf(id)?.plans[0].id ?? ""; setFirm(id); setPlan(first); fill(id, first, 0); };
  const changePlan = (id: string) => { setPlan(id); fill(firm, id, size); };
  const changeSize = (n: number) => fill(firm, plan, n);
  const ddN = Number(drawdown) || 0;
  const cap = floorCap(dd, lock, size, ddN);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setErr(null);
    if (dd === "static" && !size) { setErr("El drawdown estático necesita el tamaño (saldo inicial) de la cuenta."); return; }
    try { await onApply({ firm, plan, size, dd, drawdown: ddN, cap, target: Number(target) || 0, dayCut: Number(dayCut) || 0, dailyLoss: Number(dailyLoss) || 0, contracts: Number(contracts) || 0, buffer: Number(buffer) || 0 }, [...others]); onClose(); }
    catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); }
  };
  return (
    <form className="profile-editor" onSubmit={submit} data-testid="profile-editor">
      <div className="pe-row">
        <label>Prop firm<select value={firm} onChange={(e) => changeFirm(e.target.value)} data-testid="pe-firm">{FIRMS.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
        <label>Tipo de cuenta<select value={plan} onChange={(e) => changePlan(e.target.value)} data-testid="pe-plan">{(f?.plans ?? []).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
        <label>Tamaño<select value={size} onChange={(e) => changeSize(Number(e.target.value))} data-testid="pe-size">{(p?.sizes ?? []).map((x) => <option key={x.size} value={x.size}>{sizeLabel(x.size)}</option>)}{p && !p.sizes.some((x) => x.size === size) && size > 0 && <option value={size}>{sizeLabel(size)}</option>}</select></label>
      </div>
      {p?.note && <p className="muted small">{p.note}</p>}
      <div className="pe-row">
        <label>Tipo de drawdown<select value={dd} onChange={(e) => setDd(e.target.value as DdType)} data-testid="pe-dd">{(Object.keys(DD_LABEL) as DdType[]).map((k) => <option key={k} value={k}>{DD_LABEL[k]}</option>)}</select></label>
        <label>Drawdown máx. ($)<input type="number" min="0" step="25" value={drawdown} onChange={(e) => setDrawdown(e.target.value)} data-testid="pe-drawdown" /></label>
        {dd !== "static" && <label>Bloqueo del suelo<select value={lock} onChange={(e) => setLock(e.target.value as Lock)} data-testid="pe-lock">{(Object.keys(LOCK_LABEL) as Lock[]).map((k) => <option key={k} value={k}>{LOCK_LABEL[k]}</option>)}</select></label>}
        {dd === "static" && <label>Saldo inicial ($)<input type="number" min="0" step="1000" value={size || ""} onChange={(e) => setSize(Number(e.target.value) || 0)} data-testid="pe-start" /></label>}
      </div>
      <div className="pe-row">
        <label>Objetivo de la evaluación ($)<input type="number" min="0" step="50" value={target} placeholder="0 = sin objetivo" onChange={(e) => setTarget(e.target.value)} data-testid="pe-target" title="Ganancia neta acumulada sobre el saldo inicial (tamaño). Al llegar, el engine cierra y pausa la cuenta." /></label>
        <label>Corte del día, neto ($)<input type="number" min="0" step="50" value={dayCut} placeholder="0 = sin corte" onChange={(e) => setDayCut(e.target.value)} data-testid="pe-daycut" title="Ganancia neta del día a la que el engine cierra la posición y deja de copiar hasta mañana." /></label>
        <label>Pérdida diaria máx. ($)<input type="number" min="0" step="50" value={dailyLoss} placeholder="0 = sin límite" onChange={(e) => setDailyLoss(e.target.value)} data-testid="pe-daily" /></label>
        <label>Contratos máx.<input type="number" min="0" step="1" value={contracts} placeholder="0 = sin límite" onChange={(e) => setContracts(e.target.value)} data-testid="pe-contracts" /></label>
        <label>Colchón ($)<input type="number" min="0" step="10" value={buffer} onChange={(e) => setBuffer(e.target.value)} title="El engine pausa y cierra la cuenta cuando faltan estos dólares para el suelo (el prop firm mide tick a tick; aquí cada 2 s)" /></label>
      </div>
      <p className="muted small pe-summary" data-testid="pe-summary">
        Resultado: drawdown <b>{DD_LABEL[dd]}</b> de <b>{money(ddN)}</b>{cap ? <> · suelo {dd === "static" ? "fijo en" : "bloqueado al llegar a"} <b>{money(cap)}</b></> : " · el suelo sube siempre"}
        {Number(target) ? <> · objetivo de la evaluación {money(Number(target))} desde {money(size)}</> : null}{Number(dayCut) ? <> · corte del día {money(Number(dayCut))} neto</> : null}{Number(dailyLoss) ? <> · pérdida diaria {money(Number(dailyLoss))}</> : null}{Number(contracts) ? <> · máx. {contracts} contratos</> : null}.
        Importes orientativos del catálogo: confírmalos en la web de la firma.
      </p>
      {followers.length > 0 && (
        <div className="pe-others">
          <span className="muted small">Aplicar también a:</span>
          {followers.map((x) => <label key={x.account_id} className="inline small"><input type="checkbox" checked={others.has(x.account_id)} onChange={(e) => setOthers((prev) => { const n = new Set(prev); e.target.checked ? n.add(x.account_id) : n.delete(x.account_id); return n; })} /> {x.alias || x.account_id}</label>)}
          <button type="button" className="link tiny" onClick={() => setOthers(others.size === followers.length ? new Set() : new Set(followers.map((x) => x.account_id)))}>{others.size === followers.length ? "ninguna" : "todas"}</button>
        </div>
      )}
      {cap > 0 && a.balance > 0 && a.balance <= cap && <p className="error" data-testid="pe-warn">Atención: el saldo actual de la cuenta ({money(a.balance)}) ya está en o por debajo del suelo {money(cap)}. Si aplicas, el engine pausará la cuenta al instante. Revisa el tamaño elegido.</p>}
      {size > 0 && a.balance > 0 && (a.balance < size * 0.8 || a.balance > size * 1.5) && !(cap > 0 && a.balance <= cap) && <p className="muted small">Aviso: el saldo actual ({money(a.balance)}) no se parece al tamaño elegido ({sizeLabel(size)}). Comprueba que es el plan correcto.</p>}
      {err && <p className="error">{err}</p>}
      <div className="chips">
        <button className="primary small-btn" type="submit" disabled={busy} data-testid="pe-apply">Aplicar perfil{others.size ? ` a ${others.size + 1} cuentas` : ""}</button>
        <button className="ghost small-btn" type="button" onClick={onClose}>Cancelar</button>
        {a.firm && <button className="ghost small-btn" type="button" disabled={busy} onClick={async () => { try { await onApply({ firm: "", plan: "", size: 0, dd, drawdown: limit?.max_trailing_drawdown ?? 0, cap: limit?.drawdown_floor_cap ?? 0, target: limit?.profit_goal ?? 0, dayCut: limit?.max_daily_profit ?? 0, dailyLoss: limit?.max_daily_loss ?? 0, contracts: limit?.max_position_size ?? 0, buffer: limit?.drawdown_buffer ?? 0 }, []); onClose(); } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); } }} title="Quita la etiqueta del perfil; los límites se conservan">Quitar perfil</button>}
      </div>
    </form>
  );
}
