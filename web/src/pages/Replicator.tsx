import { useState, type FormEvent } from "react";
import { useStore } from "../lib/store";
import { time } from "../lib/format";
import { Card, Empty } from "../components/ui";
import { CopyMap } from "../components/CopyMap";

/** Copiar: mapa de cuentas (maestras y seguidoras unidas por sus reglas). Debajo, reglas avanzadas con filtro de
 *  símbolo y el flujo de replicación en vivo. */
export function Replicator() {
  const { client, rules, setRules, accounts, audit, health } = useStore();
  const [advanced, setAdvanced] = useState(false);
  const [master, setMaster] = useState("");
  const [follower, setFollower] = useState("");
  const [mult, setMult] = useState("1");
  const [symbol, setSymbol] = useState("");
  const [err, setErr] = useState<string | null>(null);
  if (!client) return null;

  async function add(e: FormEvent) {
    e.preventDefault(); setErr(null);
    try {
      const r = await client!.createRule({ master_account: master, follower_account: follower, multiplier: Number(mult) || 1,
                                           symbol_filter: symbol || null, enabled: true });
      setRules([...rules, r]); setMaster(""); setFollower(""); setSymbol("");
    } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); }
  }
  async function toggle(id: string, enabled: boolean) {
    const r = await client!.updateRule(id, { enabled });
    setRules(rules.map((x) => (x.id === id ? r : x)));
  }
  async function remove(id: string) {
    if (!confirm("¿Eliminar esta regla?")) return;
    await client!.deleteRule(id); setRules(rules.filter((x) => x.id !== id));
  }

  const ids = accounts.map((a) => a.account_id);
  const filtered = rules.filter((r) => r.symbol_filter);
  const feed = audit.filter((a) => ["MASTER_RECEIVED", "REPLICATED", "BLOCKED", "SKIPPED", "ERROR", "FOLLOWER_FILL", "FOLLOWER_REJECTED", "NO_RULE", "ACCOUNT_FILL", "DESYNC", "RESYNC", "SYNC_ORDER", "FLATTEN", "FLATTENED", "NAKED_CLOSE", "ACCOUNT_LOCKED", "GAP", "ADDON_RESTART", "DAILY_LOSS_LIMIT", "DAILY_LOSS_WARNING", "SCHEDULED_FLATTEN", "ADDON_SILENT", "ADDON_BACK", "RESUBSCRIBE", "ADDON_RECOVERY", "ENTRY_MISSED", "TRIMMED", "DAILY_PROFIT_TARGET", "DAILY_PROFIT_WARNING", "RULE_ADDED", "RULE_UPDATED", "RULE_DELETED"].includes(a.event_type)).slice(0, 25);

  return (
    <div className="grid">
      <Card title="Mapa de cuentas" icon="copy" right={<div className="chips">
        {health?.mode === "mock" && <button className="ghost small-btn" onClick={() => client.mockEvent()}>Simular operación del maestro</button>}
        <button className="chip-btn" onClick={() => setAdvanced(!advanced)} data-testid="toggle-advanced">{advanced ? "Ocultar reglas avanzadas" : `Reglas avanzadas${filtered.length ? ` · ${filtered.length}` : ""}`}</button></div>}>
        <CopyMap />
      </Card>

      {advanced && (
        <Card title="Reglas avanzadas (filtro por símbolo)" icon="list">
          <p className="muted small">Una regla con filtro copia solo ese símbolo (p. ej. únicamente NQ). En el mapa aparecen como líneas etiquetadas con el símbolo. Las reglas sin filtro se gestionan desde el mapa.</p>
          <form className="rule-form" onSubmit={add}>
            <label>Maestro<input list="accts" value={master} onChange={(e) => setMaster(e.target.value)} placeholder="Sim101" required /></label>
            <label>Seguidor<input list="accts" value={follower} onChange={(e) => setFollower(e.target.value)} placeholder="Sim102" required /></label>
            <label>Multiplicador<input type="number" step="0.1" min="0.1" value={mult} onChange={(e) => setMult(e.target.value)} /></label>
            <label>Símbolo<input value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="ej. NQ" required /></label>
            <datalist id="accts">{ids.map((i) => <option key={i} value={i} />)}</datalist>
            <button className="primary">Añadir</button>
          </form>
          {err && <p className="error">{err}</p>}
          {filtered.length === 0 ? <Empty>No hay reglas con filtro de símbolo.</Empty> : (
            <ul className="rules">
              {filtered.map((r) => (
                <li key={r.id} className={r.enabled ? "" : "off"}>
                  <div className="rule-main"><b>{r.master_account}</b> <span className="arrow">→</span> <b>{r.follower_account}</b><span className="chip">x{r.multiplier}</span><span className="chip">{r.symbol_filter}</span></div>
                  <div className="rule-actions">
                    <label className="switch"><input type="checkbox" checked={r.enabled} onChange={(e) => toggle(r.id, e.target.checked)} /><span /></label>
                    <button className="ghost danger" onClick={() => remove(r.id)}>Borrar</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <Card title="Flujo de replicación en vivo" icon="activity">
        {feed.length === 0 ? <Empty>Sin operaciones todavía.</Empty> : (
          <ul className="feed">
            {feed.map((a, i) => (
              <li key={a.id ?? i}><span className="muted">{time(a.timestamp)}</span><span className={`tag t-${a.event_type}`}>{a.event_type}</span>
                <span>{a.message}{a.target_account && <i className="muted"> → {a.target_account}</i>}</span></li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
