import { useEffect, useState } from "react";
import { useStore } from "../lib/store";
import type { Stage, StageMode } from "../lib/api";
import { Card } from "./ui";

/* Etapa (modo prueba): empezar de cero sin borrar nada (desde una fecha, o solo cuentas nuevas), ver todo cuando haga
 * falta, y, aparte, borrar el historial anterior con copia de seguridad previa. */
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const fmt = (d: string) => (d ? `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}` : "");

export function StageCard() {
  const { client, guard, refresh, readOnly, scopeAll, setScopeAll } = useStore();
  const [st, setSt] = useState<Stage | null>(null);
  const [name, setName] = useState("Prueba");
  const [mode, setMode] = useState<StageMode>("new_accounts");
  const [day, setDay] = useState(() => iso(new Date()));
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [purgeDay, setPurgeDay] = useState(() => iso(new Date()));
  const [purgeWord, setPurgeWord] = useState("");
  const load = () => client?.stage().then((s) => { setSt(s); if (s.active) { setName(s.name); setMode(s.mode); setDay(s.start_date); setPurgeDay(s.start_date); } }).catch(() => {});
  useEffect(() => { void load(); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!client || !st) return null;

  const run = async (label: string, fn: () => Promise<unknown>, ok: string) => {
    if (!(await guard())) return;
    setBusy(label); setErr(null); setMsg(null);
    try { await fn(); await load(); await refresh(); if (ok) setMsg(ok); } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(null); }
  };
  const start = () => run("start", () => client.stageSet({ action: "start", name, mode, start_date: day }), `Etapa «${name}» empezada: las estadísticas cuentan desde ${fmt(day)}.`);
  const save = () => run("save", () => client.stageSet({ action: "update", name, mode, start_date: day }), "Etapa guardada.");
  const stop = () => { if (confirm("¿Terminar la etapa? Las estadísticas vuelven a contar todo el historial. No se borra nada.")) void run("stop", () => client.stageSet({ action: "stop" }), "Etapa terminada."); };
  const toggle = (acc: string, inScope: boolean) => {
    const include = new Set(st.include), exclude = new Set(st.exclude);
    if (inScope) { include.delete(acc); exclude.add(acc); } else { exclude.delete(acc); include.add(acc); }
    void run(acc, () => client.stageSet({ action: "update", include: [...include], exclude: [...exclude] }), `${acc} ${inScope ? "fuera de" : "dentro de"} la etapa.`);
  };
  const purge = () => {
    if (purgeWord !== "BORRAR") { setErr("Escribe BORRAR en la casilla para confirmar."); return; }
    if (!confirm(`Se borrarán las operaciones, el P&L diario, saldos, comisiones y movimientos de caja ANTERIORES al ${fmt(purgeDay)} de TODAS las cuentas.\n\nAntes se hace una copia de seguridad (Riesgo → Copias). La auditoría, las cuentas y los límites se conservan.\n\n¿Seguir?`)) return;
    void run("purge", async () => { const r = await client.stageReset(purgeDay, purgeWord); setPurgeWord(""); setMsg(`Borrado: ${Object.entries(r.deleted).map(([k, v]) => `${k} ${v}`).join(", ")}${r.backup?.file ? ` · copia previa: ${r.backup.file}` : ""}`); }, "");
  };
  const tr = st.counts;
  const title = st.active ? `Etapa «${st.name}» · desde ${fmt(st.start_date)} · ${st.mode === "new_accounts" ? "solo cuentas nuevas" : "todas las cuentas"}` : "Etapa: contando todo el historial";
  return (
    <Card title={title} icon="layers" className={st.active ? "alerts-card" : ""}
          right={<span className="chips">
            {st.active && <button className={`chip-btn ${scopeAll ? "active" : ""}`} onClick={() => setScopeAll(!scopeAll)} data-testid="stage-scope">{scopeAll ? "Viendo todo · volver a la etapa" : "Ver todo el historial"}</button>}
            <button className="chip-btn" onClick={() => setOpen(!open)} data-testid="stage-open">{open ? "Cerrar" : st.active ? "Ajustar etapa" : "Empezar una etapa"}</button>
          </span>}>
      <p className="muted small" style={{ margin: 0 }} data-testid="stage-summary">
        {st.active
          ? <>Calendario, Análisis, informes, coach e Inicio cuentan <b>{tr.trades_stage ?? "…"}</b> operaciones de la etapa (de {tr.trades_total ?? "…"} en total). El historial anterior sigue guardado: {scopeAll ? "ahora lo estás viendo todo." : "«Ver todo el historial» lo muestra."}</>
          : <>Para empezar de cero sin borrar nada (modo prueba, cuentas nuevas, nuevo mes de evaluación…), empieza una etapa: desde esa fecha solo cuentan las operaciones nuevas y, si quieres, solo las cuentas que aparezcan a partir de entonces.</>}
      </p>
      {open && (
        <div className="stage-form" data-testid="stage-form">
          <div className="rule-form">
            <label>Nombre<input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} data-testid="stage-name" /></label>
            <label>Desde el día<input type="date" value={day} max={iso(new Date())} onChange={(e) => setDay(e.target.value)} data-testid="stage-day" /></label>
            <label>Qué cuenta
              <select value={mode} onChange={(e) => setMode(e.target.value as StageMode)} data-testid="stage-mode">
                <option value="new_accounts">Solo cuentas nuevas desde esa fecha (las de antes quedan fuera)</option>
                <option value="date">Todas las cuentas, pero solo desde esa fecha</option>
              </select>
            </label>
            {!st.active
              ? <button className="primary" disabled={busy !== null || readOnly} onClick={() => void start()} data-testid="stage-start">{busy === "start" ? "…" : "Empezar etapa"}</button>
              : <span className="chips"><button className="primary" disabled={busy !== null || readOnly} onClick={() => void save()}>Guardar</button><button className="ghost" disabled={busy !== null || readOnly} onClick={stop} data-testid="stage-stop">Terminar etapa</button></span>}
          </div>
          {st.active && st.mode === "new_accounts" && (
            <div>
              <p className="muted small">Cuentas: las marcadas <b>nueva</b> aparecieron después de empezar la etapa y cuentan solas; las <b>anteriores</b> no cuentan salvo que las incluyas. Pulsa una para cambiarla.</p>
              <div className="chips" data-testid="stage-accounts">
                {st.accounts.map((a) => (
                  <button key={a.account_id} className={`chip-btn ${a.in_scope ? "active" : ""}`} disabled={busy !== null || readOnly} onClick={() => toggle(a.account_id, a.in_scope)} title={a.in_scope ? "Dentro de la etapa: pulsa para dejarla fuera" : "Fuera de la etapa: pulsa para incluirla"} data-testid={`stage-acc-${a.account_id}`}>
                    {a.alias ? `${a.alias} (${a.account_id})` : a.account_id} · {a.is_new ? "nueva" : "anterior"} · {a.in_scope ? "cuenta" : "fuera"}
                  </button>
                ))}
              </div>
            </div>
          )}
          <details className="danger-zone">
            <summary className="bad small">Borrar el historial anterior (irreversible; se hace copia antes)</summary>
            <p className="muted small">Borra operaciones, P&L diario, saldos, comisiones y movimientos de caja anteriores a la fecha, de todas las cuentas. La auditoría, las cuentas, reglas y límites se conservan. Antes se guarda una copia de seguridad (Riesgo → Copias de seguridad). Solo si de verdad no quieres volver a ver lo anterior: la etapa ya lo oculta sin borrarlo.</p>
            <div className="rule-form">
              <label>Borrar lo anterior al<input type="date" value={purgeDay} max={iso(new Date())} onChange={(e) => setPurgeDay(e.target.value)} data-testid="purge-day" /></label>
              <label>Escribe BORRAR<input value={purgeWord} onChange={(e) => setPurgeWord(e.target.value)} placeholder="BORRAR" data-testid="purge-word" /></label>
              <button className="danger-solid" disabled={busy !== null || readOnly || purgeWord !== "BORRAR"} onClick={purge} data-testid="purge-run">{busy === "purge" ? "…" : "Borrar historial anterior"}</button>
            </div>
          </details>
        </div>
      )}
      {msg && <p className="ok small" data-testid="stage-msg">{msg}</p>}
      {err && <p className="bad small" data-testid="stage-err">{err}</p>}
    </Card>
  );
}
