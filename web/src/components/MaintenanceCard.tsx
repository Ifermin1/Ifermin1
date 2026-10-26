import { useEffect, useRef, useState } from "react";
import type { BackupState, Client } from "../lib/api";
import { useStore } from "../lib/store";
import { Card } from "./ui";

/* Copias de seguridad de la base de datos y exportar/importar la configuración (sin secretos). */
const dl = (blob: Blob, filename: string) => { const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = filename; a.click(); window.setTimeout(() => URL.revokeObjectURL(a.href), 2000); };
const kb = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`);

export function MaintenanceCard({ client }: { client: Client }) {
  const { readOnly, guard } = useStore();
  const [st, setSt] = useState<BackupState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [replace, setReplace] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const load = () => client.backup().then(setSt).catch(() => {});
  useEffect(() => { void load(); }, [client]);   // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (what: string, fn: () => Promise<string | null>) => { setBusy(what); setMsg(null); try { const m = await fn(); if (m) setMsg(m); await load(); } catch (ex) { setMsg((ex instanceof Error ? ex.message : String(ex)).replace(/^\d+:\s*/, "")); } finally { setBusy(null); } };
  const backupNow = () => run("backup", async () => { const r = await client.backupRun(); return `Copia hecha: ${r.name} (${kb(r.size)}).`; });
  const download = (name: string) => run(name, async () => { const { blob } = await client.backupDownload(name); dl(blob, name); return null; });
  const exportCfg = () => run("export", async () => { const { blob, filename } = await client.configExport(); dl(blob, filename); return "Configuración exportada (sin tokens ni claves)."; });
  const importCfg = async (f: File) => {
    let data: unknown;
    try { data = JSON.parse(await f.text()); } catch { setMsg("El archivo no es un JSON válido."); return; }
    if (!(await guard()) || !confirm(`¿Importar la configuración de ${f.name}?\n\nSe aplican reglas, límites, presets, horario, comisiones, ajustes de cuentas, plantillas y avisos${replace ? ". Las reglas actuales se BORRAN antes" : " (las reglas existentes se actualizan, no se duplican)"}.`)) return;
    await run("import", async () => { const r = await client.configImport(data, replace); return `Importado: ${r.rules} reglas, ${r.risk_limits} límites, ${r.accounts} cuentas${r.other.length ? ", " + r.other.join(", ") : ""}. Recarga la consola para ver todo.`; });
  };
  return (
    <Card title="Copias de seguridad y configuración" right={st && <span className={`badge ${st.integrity === "ok" ? "ok" : st.integrity ? "bad" : ""}`}>{st.integrity === "ok" ? "base de datos íntegra" : st.integrity ? "base de datos DAÑADA" : "…"}</span>}>
      <div className="notify-grid">
        <div>
          <p className="muted small"><b>Copias de la base de datos</b> (operaciones, auditoría, reglas, límites): una al día, se guardan las últimas {st?.keep ?? 14} en <code>engine\data\backups</code>. Para restaurar: parar el engine y copiar el archivo sobre <code>engine\data\tradepilot.db</code>.</p>
          <div className="chips">
            <button className="primary small-btn" disabled={busy !== null || readOnly || !st?.available} onClick={() => void backupNow()} data-testid="backup-run">{busy === "backup" ? "Copiando…" : "Copia ahora"}</button>
            <span className="muted small">{st ? (st.available ? `última: ${st.last_at ? st.last_at.slice(0, 16).replace("T", " ") : "ninguna"}${st.last_error ? ` · error: ${st.last_error}` : ""}` : "base en memoria: sin copias") : ""}</span>
          </div>
          {st && st.files.length > 0 && <ul className="backup-list" data-testid="backup-list">
            {st.files.slice(0, 8).map((f) => <li key={f.name}><span>{f.at.slice(0, 16).replace("T", " ")}</span><span className="muted">{kb(f.size)}</span><button className="link tiny" disabled={busy !== null || readOnly} onClick={() => void download(f.name)}>descargar</button></li>)}
          </ul>}
        </div>
        <div>
          <p className="muted small"><b>Configuración</b>: reglas, límites, presets, horario, comisiones, pérdida del día, alias y etiquetas de cuentas, plantillas, parámetros de análisis y qué avisos mandar. Sin tokens ni claves. Sirve para pasar a otro PC o recuperar tras reinstalar.</p>
          <div className="chips">
            <button className="small-btn" disabled={busy !== null} onClick={() => void exportCfg()} data-testid="config-export">{busy === "export" ? "…" : "Exportar configuración"}</button>
            <button className="small-btn" disabled={busy !== null || readOnly} onClick={() => file.current?.click()} data-testid="config-import">{busy === "import" ? "Importando…" : "Importar…"}</button>
            <input ref={file} type="file" accept="application/json,.json" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void importCfg(f); }} />
            <label className="inline small"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> borrar las reglas actuales antes de importar</label>
          </div>
        </div>
      </div>
      {msg && <p className="muted small" data-testid="maint-msg">{msg}</p>}
    </Card>
  );
}
