import { useState, type ReactNode } from "react";
import { useStore } from "../lib/store";
import { Badge } from "./ui";
import { Icon, type IconName } from "./Icons";
import { livePnl } from "./AccountPanel";
import { signedMoney } from "../lib/format";

export type Route = "dashboard" | "performance" | "accounts" | "replicator" | "risk" | "audit";

const NAV: { id: Route; label: string; icon: IconName }[] = [
  { id: "dashboard", label: "Inicio", icon: "home" },
  { id: "performance", label: "Calendario", icon: "calendar" },
  { id: "accounts", label: "Cuentas", icon: "users" },
  { id: "replicator", label: "Copiar", icon: "copy" },
  { id: "risk", label: "Riesgo", icon: "shield" },
  { id: "audit", label: "Auditoría", icon: "list" },
];

/** Resumen siempre visible: P&L del día de las cuentas en juego, cuántas copian y el kill switch. */
export function StatusStrip({ className = "" }: { className?: string }) {
  const { client, accounts, rules, risk, health, prices, setRisk } = useStore();
  const [busy, setBusy] = useState(false);
  const master = health?.bridge.master_account ?? null;
  const copying = accounts.filter((a) => a.enabled && a.account_id !== master && rules.some((r) => r.enabled && r.follower_account.toLowerCase() === a.account_id.toLowerCase()));
  const inPlay = accounts.filter((a) => a.enabled && (a.account_id === master || copying.includes(a)));
  let total = 0; let est = false;
  let fees = 0;
  for (const a of inPlay) { const p = livePnl(a, prices); total += p.value - (a.commissions_today || 0); fees += a.commissions_today || 0; est ||= p.live; }
  const open = inPlay.reduce((s, a) => s + a.open_positions.length, 0);
  async function toggleKill() {
    if (!client) return;
    const active = !risk?.kill_switch;
    if (active && !confirm("¿KILL SWITCH? Se bloquean todas las réplicas y se cierran a mercado las posiciones de todas las cuentas.")) return;
    setBusy(true);
    try { setRisk(await client.killSwitch(active, active ? "desde la barra de estado" : undefined, active, true)); }
    catch (ex) { alert(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(false); }
  }
  return (
    <div className={`strip ${className}`}>
      <div className="strip-item">
        <span className="stat-label">P&L hoy{fees ? " neto" : ""}</span>
        <b className={`num ${total > 0 ? "ok" : total < 0 ? "bad" : ""}`} title={fees ? `comisiones del día: ${fees.toFixed(2)} $` : undefined}>{est && "≈ "}{signedMoney(total)}</b>
      </div>
      <div className="strip-item"><span className="stat-label">Copiando</span><b>{copying.length}</b></div>
      <div className="strip-item"><span className="stat-label">Posiciones</span><b className={open ? "warn" : ""}>{open}</b></div>
      <button className={risk?.kill_switch ? "primary small-btn" : "danger-solid small-btn"} disabled={busy || !client} onClick={() => void toggleKill()}>
        {risk?.kill_switch ? "Reanudar" : "DETENER"}
      </button>
    </div>
  );
}

export function Shell({ route, onRoute, children }: { route: Route; onRoute: (r: Route) => void; children: ReactNode }) {
  const { health, wsStatus, risk, logout, session, density, setDensity } = useStore();
  const connected = health?.bridge.connected ?? null;
  return (
    <div className="shell">
      <aside className="sidenav">
        <div className="brand"><span className="brand-dot" /><span className="brand-text">TradePilot X</span></div>
        <nav>
          {NAV.map((n) => (
            <button key={n.id} className={route === n.id ? "active" : ""} onClick={() => onRoute(n.id)}>
              <span className="nav-icon"><Icon name={n.icon} size={16} /></span>{n.label}
            </button>
          ))}
        </nav>
        <div className="sidenav-foot">
          <div className="chips">
            <Badge ok={connected}>{health ? (health.mode === "mock" ? "Simulador" : "NinjaTrader") : "…"}</Badge>
            <Badge ok={wsStatus === "open" ? true : wsStatus === "connecting" ? null : false}>en vivo</Badge>
          </div>
          <div className="muted small">{session?.baseUrl}</div>
          <button className="link" onClick={logout}>Salir</button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar-title">{NAV.find((n) => n.id === route)?.label}</div>
          <StatusStrip className="strip-desktop" />
          <div className="topbar-status">
            {risk?.kill_switch && <span className="badge bad blink">KILL SWITCH</span>}
            <Badge ok={connected}>{health ? (health.mode === "mock" ? "Simulador" : "NinjaTrader") : "…"} {connected ? "conectado" : "sin datos"}</Badge>
            <Badge ok={wsStatus === "open" ? true : wsStatus === "connecting" ? null : false}>en vivo</Badge>
            <button className={`ghost icon-btn ${density === "compact" ? "active" : ""}`} title={density === "compact" ? "Vista amplia" : "Vista compacta"}
                    aria-pressed={density === "compact"} onClick={() => setDensity(density === "compact" ? "cozy" : "compact")}>{density === "compact" ? "▤" : "▥"}</button>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>

      <StatusStrip className="strip-mobile" />
      <nav className="tabbar">
        {NAV.map((n) => (
          <button key={n.id} className={route === n.id ? "active" : ""} onClick={() => onRoute(n.id)}>
            <span className="nav-icon"><Icon name={n.icon} size={18} /></span><span>{n.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
