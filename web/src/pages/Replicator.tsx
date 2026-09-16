import { useState, type FormEvent } from "react";
import { useStore } from "../lib/store";
import { time } from "../lib/format";
import { Card, Empty } from "../components/ui";

export function Replicator() {
  const { client, rules, setRules, accounts, audit, health } = useStore();
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
  const feed = audit.filter((a) => ["MASTER_RECEIVED", "REPLICATED", "BLOCKED", "SKIPPED", "ERROR", "FOLLOWER_FILL", "FOLLOWER_REJECTED", "NO_RULE", "ACCOUNT_FILL", "DESYNC", "RESYNC", "SYNC_ORDER", "FLATTEN", "FLATTENED", "NAKED_CLOSE", "GAP", "ADDON_RESTART", "DAILY_LOSS_LIMIT", "DAILY_LOSS_WARNING", "SCHEDULED_FLATTEN", "ADDON_SILENT", "ADDON_BACK", "RESUBSCRIBE", "ADDON_RECOVERY", "ENTRY_MISSED", "TRIMMED", "DAILY_PROFIT_TARGET", "DAILY_PROFIT_WARNING"].includes(a.event_type)).slice(0, 25);

  return (
    <div className="grid">
      <Card title="Nueva regla de copia">
        <form className="rule-form" onSubmit={add}>
          <label>Maestro<input list="accts" value={master} onChange={(e) => setMaster(e.target.value)} placeholder="Sim101" required /></label>
          <label>Seguidor<input list="accts" value={follower} onChange={(e) => setFollower(e.target.value)} placeholder="Sim102" required /></label>
          <label>Multiplicador<input type="number" step="0.1" min="0.1" value={mult} onChange={(e) => setMult(e.target.value)} /></label>
          <label>Símbolo (opcional)<input value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="Todos · ej. NQ" /></label>
          <datalist id="accts">{ids.map((i) => <option key={i} value={i} />)}</datalist>
          <button className="primary">Añadir</button>
        </form>
        {err && <p className="error">{err}</p>}
      </Card>

      <Card title="Reglas" right={health?.mode === "mock" && (
        <button className="ghost" onClick={() => client.mockEvent()}>Simular operación del maestro</button>)}>
        {rules.length === 0 ? <Empty>No hay reglas. Crea la primera arriba.</Empty> : (
          <ul className="rules">
            {rules.map((r) => (
              <li key={r.id} className={r.enabled ? "" : "off"}>
                <div className="rule-main">
                  <b>{r.master_account}</b> <span className="arrow">→</span> <b>{r.follower_account}</b>
                  <span className="chip">x{r.multiplier}</span>
                  <span className="chip">{r.symbol_filter ?? "todos los símbolos"}</span>
                </div>
                <div className="rule-actions">
                  <label className="switch"><input type="checkbox" checked={r.enabled} onChange={(e) => toggle(r.id, e.target.checked)} /><span /></label>
                  <button className="ghost danger" onClick={() => remove(r.id)}>Borrar</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Flujo de replicación en vivo">
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
