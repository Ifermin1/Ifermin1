import type { ReactNode } from "react";
import { useStore } from "../lib/store";
import { Badge } from "./ui";

export type Route = "dashboard" | "accounts" | "replicator" | "risk" | "audit";

const NAV: { id: Route; label: string; icon: string }[] = [
  { id: "dashboard", label: "Inicio", icon: "▦" },
  { id: "accounts", label: "Cuentas", icon: "◎" },
  { id: "replicator", label: "Copiar", icon: "⧉" },
  { id: "risk", label: "Riesgo", icon: "⚠" },
  { id: "audit", label: "Auditoría", icon: "≡" },
];

export function Shell({ route, onRoute, children }: { route: Route; onRoute: (r: Route) => void; children: ReactNode }) {
  const { health, wsStatus, risk, logout, session } = useStore();
  const connected = health?.bridge.connected ?? null;
  return (
    <div className="shell">
      <aside className="sidenav">
        <div className="brand"><span className="brand-dot" />TradePilot X</div>
        <nav>
          {NAV.map((n) => (
            <button key={n.id} className={route === n.id ? "active" : ""} onClick={() => onRoute(n.id)}>
              <span className="nav-icon">{n.icon}</span>{n.label}
            </button>
          ))}
        </nav>
        <div className="sidenav-foot">
          <div className="muted small">{session?.baseUrl}</div>
          <button className="link" onClick={logout}>Salir</button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="topbar-title">{NAV.find((n) => n.id === route)?.label}</div>
          <div className="topbar-status">
            {risk?.kill_switch && <span className="badge bad blink">KILL SWITCH</span>}
            <Badge ok={connected}>{health ? (health.mode === "mock" ? "Simulador" : "NinjaTrader") : "…"} {connected ? "conectado" : "sin datos"}</Badge>
            <Badge ok={wsStatus === "open" ? true : wsStatus === "connecting" ? null : false}>en vivo</Badge>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>

      <nav className="tabbar">
        {NAV.map((n) => (
          <button key={n.id} className={route === n.id ? "active" : ""} onClick={() => onRoute(n.id)}>
            <span className="nav-icon">{n.icon}</span><span>{n.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
