import { useState } from "react";
import { useStore } from "../lib/store";
import { Card, Empty } from "../components/ui";

export function Audit() {
  const { audit } = useStore();
  const [filter, setFilter] = useState("");
  const types = Array.from(new Set(audit.map((a) => a.event_type))).sort();
  const rows = filter ? audit.filter((a) => a.event_type === filter) : audit;
  return (
    <Card title="Auditoría" right={
      <select value={filter} onChange={(e) => setFilter(e.target.value)}>
        <option value="">Todos los tipos</option>{types.map((t) => <option key={t}>{t}</option>)}
      </select>}>
      {rows.length === 0 ? <Empty>Nada que mostrar.</Empty> : (
        <div className="table-wrap"><table className="audit">
          <thead><tr><th>Hora</th><th>Tipo</th><th>Origen</th><th>Destino</th><th>Mensaje</th></tr></thead>
          <tbody>{rows.map((a, i) => (
            <tr key={a.id ?? i}>
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
