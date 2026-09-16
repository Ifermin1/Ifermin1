import { useState } from "react";
import { useStore } from "../lib/store";
import type { AuditEvent } from "../lib/api";
import { Card, Empty } from "../components/ui";

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
  const { audit } = useStore();
  const [filter, setFilter] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const types = Array.from(new Set(audit.map((a) => a.event_type))).sort();
  const rows = filter ? audit.filter((a) => a.event_type === filter) : audit;
  const asText = () => [...rows].reverse().map(fmt).join("\n");   // cronológico: lo más antiguo arriba
  const stamp = () => new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");

  async function copyAll() {
    const ok = await copyText(asText());
    setNote(ok ? `${rows.length} líneas copiadas al portapapeles.` : "No se pudo copiar: usa Descargar .txt.");
    setTimeout(() => setNote(null), 4000);
  }
  function download() {
    const blob = new Blob([asText() + "\n"], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `auditoria-${filter || "todo"}-${stamp()}.txt`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copyRow(a: AuditEvent) {
    const ok = await copyText(fmt(a));
    setNote(ok ? "Línea copiada." : "No se pudo copiar.");
    setTimeout(() => setNote(null), 2500);
  }

  return (
    <Card title="Auditoría" right={
      <div className="audit-tools">
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">Todos los tipos</option>{types.map((t) => <option key={t}>{t}</option>)}
        </select>
        <button className="ghost small-btn" title="Copiar todo lo visible como texto" disabled={rows.length === 0} onClick={() => void copyAll()}>⧉ Copiar</button>
        <button className="ghost small-btn" title="Descargar lo visible como archivo .txt" disabled={rows.length === 0} onClick={download}>⤓ .txt</button>
      </div>}>
      {note && <p className="muted small">{note}</p>}
      {rows.length === 0 ? <Empty>Nada que mostrar.</Empty> : (
        <div className="table-wrap"><table className="audit">
          <thead><tr><th></th><th>Hora</th><th>Tipo</th><th>Origen</th><th>Destino</th><th>Mensaje</th></tr></thead>
          <tbody>{rows.map((a, i) => (
            <tr key={a.id ?? i}>
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
