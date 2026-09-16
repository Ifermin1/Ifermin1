import { useMemo, useState } from "react";
import { useStore } from "../lib/store";
import type { AuditEvent } from "../lib/api";
import { Card, Empty } from "../components/ui";

type Family = "copia" | "bloqueos" | "errores" | "riesgo" | "sistema";
const FAMILIES: Record<Exclude<Family, "sistema">, string[]> = {
  copia: ["MASTER_RECEIVED", "REPLICATED", "FOLLOWER_FILL", "FOLLOWER_STATUS", "ACCOUNT_FILL", "SYNC_ORDER", "RESYNC", "ENTRY_FILLED"],
  bloqueos: ["BLOCKED", "SKIPPED", "TRIMMED", "NO_RULE", "ENTRY_MISSED", "DUPLICATE"],
  errores: ["ERROR", "FOLLOWER_REJECTED", "NAKED_CLOSE", "GAP", "DESYNC"],
  riesgo: ["KILL_SWITCH_ON", "KILL_SWITCH_OFF", "DAILY_LOSS_WARNING", "DAILY_LOSS_LIMIT", "DAILY_PROFIT_WARNING", "DAILY_PROFIT_TARGET",
           "FLATTEN", "FLATTENED", "SCHEDULED_FLATTEN", "SESSION_CLOSED", "SESSION_REOPENED", "RISK_LIMIT_SET", "SCHEDULE_SET"],
};
const FAMILY_LABEL: Record<Family, string> = { copia: "Copia", bloqueos: "Bloqueos", errores: "Errores", riesgo: "Riesgo", sistema: "Sistema" };
export const familyOf = (t: string): Family => (Object.keys(FAMILIES) as Exclude<Family, "sistema">[]).find((f) => FAMILIES[f].includes(t)) ?? "sistema";
const IMPORTANT: Family[] = ["bloqueos", "errores", "riesgo"];

function fmt(a: AuditEvent): string {
  const when = new Date(a.timestamp).toLocaleString("es-ES", { hour12: false });
  const route = a.source_account || a.target_account ? ` | ${a.source_account ?? "-"} -> ${a.target_account ?? "-"}` : "";
  return `${when} | ${a.event_type}${route} | ${a.message}`;
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* cae al método clásico (http en la LAN no expone el portapapeles moderno) */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

export function Audit() {
  const { audit, accounts } = useStore();
  const [type, setType] = useState("");
  const [account, setAccount] = useState("");
  const [q, setQ] = useState("");
  const [fams, setFams] = useState<Set<Family>>(new Set());
  const [important, setImportant] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const types = useMemo(() => Array.from(new Set(audit.map((a) => a.event_type))).sort(), [audit]);
  const accountIds = useMemo(() => {
    const s = new Set<string>(); for (const a of audit) { if (a.source_account) s.add(a.source_account); if (a.target_account) s.add(a.target_account); }
    return [...s].sort();
  }, [audit]);
  const alias = (id: string) => { const a = accounts.find((x) => x.account_id === id); return a?.alias ? `${a.alias} · ${id}` : id; };

  const needle = q.trim().toLowerCase();
  const rows = audit.filter((a) => {
    if (type && a.event_type !== type) return false;
    if (account && a.source_account !== account && a.target_account !== account) return false;
    const fam = familyOf(a.event_type);
    if (important && !IMPORTANT.includes(fam)) return false;
    if (fams.size && !fams.has(fam)) return false;
    if (needle && !`${a.event_type} ${a.source_account ?? ""} ${a.target_account ?? ""} ${a.message}`.toLowerCase().includes(needle)) return false;
    return true;
  });
  const filtered = rows.length !== audit.length;
  const reset = () => { setType(""); setAccount(""); setQ(""); setFams(new Set()); setImportant(false); };
  const asText = () => [...rows].reverse().map(fmt).join("\n");   // cronológico: lo más antiguo arriba
  const stamp = () => new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const flash = (m: string, ms = 3000) => { setNote(m); setTimeout(() => setNote(null), ms); };

  async function copyAll() { flash((await copyText(asText())) ? `${rows.length} líneas copiadas al portapapeles.` : "No se pudo copiar: usa Descargar .txt.", 4000); }
  function download() {
    const blob = new Blob([asText() + "\n"], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `auditoria-${type || (filtered ? "filtro" : "todo")}-${stamp()}.txt`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copyRow(a: AuditEvent) { flash((await copyText(fmt(a))) ? "Línea copiada." : "No se pudo copiar.", 2500); }

  return (
    <Card title={`Auditoría · ${filtered ? `${rows.length} de ${audit.length}` : audit.length}`} right={
      <div className="audit-tools">
        <button className="ghost small-btn" title="Copiar lo visible como texto" disabled={rows.length === 0} onClick={() => void copyAll()}>⧉ Copiar</button>
        <button className="ghost small-btn" title="Descargar lo visible como archivo .txt" disabled={rows.length === 0} onClick={download}>⤓ .txt</button>
      </div>}>
      <div className="filters">
        <input className="filter-q" placeholder="Buscar en mensajes, cuentas o tipos…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={account} onChange={(e) => setAccount(e.target.value)}>
          <option value="">Todas las cuentas</option>{accountIds.map((id) => <option key={id} value={id}>{alias(id)}</option>)}
        </select>
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">Todos los tipos</option>{types.map((t) => <option key={t}>{t}</option>)}
        </select>
        <div className="chips">
          {(Object.keys(FAMILY_LABEL) as Family[]).map((f) => (
            <button key={f} className={`chip-btn fam-${f} ${fams.has(f) ? "active" : ""}`}
                    onClick={() => setFams((s) => { const n = new Set(s); n.has(f) ? n.delete(f) : n.add(f); return n; })}>{FAMILY_LABEL[f]}</button>
          ))}
          <button className={`chip-btn ${important ? "active" : ""}`} onClick={() => setImportant(!important)} title="Bloqueos, errores y riesgo">⚑ Solo importantes</button>
          {filtered && <button className="chip-btn" onClick={reset}>✕ Limpiar</button>}
        </div>
      </div>
      {note && <p className="muted small">{note}</p>}
      {rows.length === 0 ? <Empty>{audit.length ? "Nada coincide con el filtro." : "Nada que mostrar."}</Empty> : (
        <div className="table-wrap"><table className="audit">
          <thead><tr><th></th><th>Hora</th><th>Tipo</th><th>Origen</th><th>Destino</th><th>Mensaje</th></tr></thead>
          <tbody>{rows.map((a, i) => (
            <tr key={a.id ?? i} className={`fam-${familyOf(a.event_type)}`}>
              <td><button className="ghost copy-btn" title="Copiar esta línea" onClick={() => void copyRow(a)}>⧉</button></td>
              <td className="muted nowrap">{new Date(a.timestamp).toLocaleString("es-ES", { hour12: false })}</td>
              <td><span className={`tag t-${a.event_type}`}>{a.event_type}</span></td>
              <td>{a.source_account ?? "—"}</td><td>{a.target_account ?? "—"}</td><td>{a.message}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </Card>
  );
}
