import { useState } from "react";
import { useStore } from "../lib/store";
import type { Account, RiskLimit } from "../lib/api";
import { Card } from "./ui";
import type { Route } from "./Shell";

/* Cuentas en pausa por riesgo: por qué se pausó cada una (con las cifras), qué hace Reanudar y el botón para hacerlo aquí
 * mismo. Si el engine se niega (la cuenta sigue dentro del colchón), el motivo aparece debajo con el siguiente paso. */
/** El engine responde {"detail": "..."}: se enseña solo el texto. */
function errorText(ex: unknown): string {
  const raw = ex instanceof Error ? ex.message : String(ex);
  try { const j = JSON.parse(raw); if (j && typeof j.detail === "string") return j.detail; } catch { /* texto plano */ }
  return raw;
}
const usd = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v.toLocaleString("es-ES", { maximumFractionDigits: 0 })} $`);

function explain(l: RiskLimit, a: Account | undefined): { title: string; why: string; next: string; tone: "bad" | "ok" | "warn" } {
  const dd = a?.drawdown;
  switch (l.halted_reason) {
    case "drawdown":
      return { tone: "bad", title: "Límite de drawdown",
        why: dd && dd.floor !== null ? `La cuenta vale ${usd(dd.equity)}; el suelo del prop firm está en ${usd(dd.floor)} (máximo ${usd(dd.peak)} − ${usd(l.max_trailing_drawdown)}) y quedan ${usd(dd.room)}. Con un colchón de ${usd(l.drawdown_buffer)} el engine la pausó y cerró para que no la cierre el prop firm.`
          : `Se acercó a menos de ${usd(l.drawdown_buffer)} del suelo del drawdown (${usd(l.max_trailing_drawdown)} desde su máximo).`,
        next: dd && dd.room !== null && dd.room <= l.drawdown_buffer ? "Sigue dentro del colchón: Reanudar será rechazado. Si el límite no corresponde con esta cuenta (tamaño o tipo de plan mal deducidos), edítalo en Riesgo: sube el drawdown máximo, baja el colchón o quita el límite."
          : "Ya tiene margen: Reanudar vuelve a copiar. Si salta otra vez, baja tamaño en esta cuenta." };
    case "daily_loss":
      return { tone: "bad", title: "Límite de pérdida diaria", why: `Hoy va ${usd(a?.net_pnl)} neto y el límite es −${usd(l.max_daily_loss)}. Se cerró y se dejó de copiar para no agrandar el día.`,
        next: a && a.net_pnl <= -l.max_daily_loss ? "Sigue por debajo del límite: Reanudar será rechazado hoy. Si de verdad quieres seguir, sube el límite en Riesgo (bajo tu responsabilidad)." : "Reanudar vuelve a copiar hoy; no volverá a saltar salvo que la pérdida siga creciendo." };
    case "daily_profit":
      return { tone: "ok", title: "Corte del día hecho", why: `Hoy va ${usd(a?.net_pnl)} neto y el objetivo diario era +${usd(l.max_daily_profit)}: ganancia asegurada, cerrada y sin copiar hasta mañana.`, next: "Reanudar la vuelve a poner a copiar hoy (si sigue por encima del objetivo, súbelo o quítalo en Riesgo)." };
    case "profit_goal":
      return { tone: "ok", title: "Objetivo de la evaluación alcanzado", why: `Lleva +${usd(l.profit_goal)} o más sobre el saldo inicial de ${usd(l.start_balance)}: evaluación superada, cerrada y pausada para no perderla.`, next: "Para seguir operándola, sube o quita el objetivo en Riesgo y reanuda." };
    case "tilt":
      return { tone: "warn", title: "Pausa por tilt", why: "El detector de tilt vio señales de operar por rabia (racha, tamaño creciente, reentradas) y la pausó unos minutos.", next: "Se reanuda sola al acabar la pausa; Reanudar la levanta ya." };
    default:
      return { tone: "warn", title: "Pausa manual", why: "Alguien la pausó desde Riesgo (o por Telegram).", next: "Reanudar vuelve a copiar." };
  }
}

export function PausedAccountsCard({ onRoute }: { onRoute?: (r: Route) => void }) {
  const { client, risk, accounts, guard, refresh, readOnly, setRisk } = useStore();
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const paused = (risk?.limits ?? []).filter((l) => l.trading_halted);
  if (paused.length === 0) return null;
  const resume = async (l: RiskLimit) => {
    if (!client || !(await guard())) return;
    if (!confirm(`¿Reanudar ${l.account_id}? Volverá a copiar las operaciones de la maestra.`)) return;
    setBusy(l.account_id); setErrors((e) => ({ ...e, [l.account_id]: "" }));
    try {
      // siempre sobre el límite tal y como está ahora en el engine (puede haberse editado en Riesgo hace un momento)
      const fresh = (await client.risk()).limits.find((x) => x.account_id === l.account_id) ?? l;
      const { halted_reason: _r, halted_at: _a, ...body } = fresh; // eslint-disable-line @typescript-eslint/no-unused-vars
      await client.upsertLimit({ ...body, trading_halted: false });
      setRisk(await client.risk()); await refresh();
    } catch (ex) { setErrors((e) => ({ ...e, [l.account_id]: errorText(ex) })); } finally { setBusy(null); }
  };
  const bad = paused.some((l) => explain(l, accounts.find((a) => a.account_id === l.account_id)).tone === "bad");
  return (
    <Card title={`Cuentas en pausa · ${paused.length}`} icon="shield" className={`alerts-card ${bad ? "has-bad" : ""}`}
          right={<span className="muted small">mientras una cuenta está en pausa, nada se le copia (BLOCKED en Auditoría)</span>}>
      <ul className="alerts" data-testid="paused-list">
        {paused.map((l) => {
          const a = accounts.find((x) => x.account_id === l.account_id);
          const ex = explain(l, a);
          return (
            <li key={l.account_id} className={`alert ${ex.tone}`} data-testid={`paused-${l.account_id}`}>
              <div className="alert-main">
                <b>{a?.alias ? `${a.alias} (${l.account_id})` : l.account_id} · {ex.title}{l.halted_at ? ` · desde ${new Date(l.halted_at).toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}` : ""}</b>
                <span className="muted">{ex.why}</span>
                <span className="small"><b>Qué hace Reanudar:</b> {ex.next}</span>
                {errors[l.account_id] && <span className="bad small" data-testid="paused-error"><b>El engine no la reanudó:</b> {errors[l.account_id]}</span>}
              </div>
              <div className="chips">
                <button className="chip-btn" disabled={busy !== null || readOnly} onClick={() => void resume(l)} data-testid={`resume-${l.account_id}`}>{busy === l.account_id ? "…" : "Reanudar"}</button>
                <button className="ghost small-btn" onClick={() => onRoute?.("risk")}>Editar límite en Riesgo</button>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
