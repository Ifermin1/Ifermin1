import { useEffect, useState } from "react";
import { StoreProvider, useStore } from "./lib/store";
import { Shell, type Route } from "./components/Shell";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { Trade } from "./pages/Trade";
import { Performance } from "./pages/Performance";
import { Analysis } from "./pages/Analysis";
import { Accounts } from "./pages/Accounts";
import { Risk } from "./pages/Risk";
import { Audit } from "./pages/Audit";
import { Pocket } from "./pages/Pocket";
import { LockOverlay } from "./components/LockOverlay";

const ROUTES: Route[] = ["dashboard", "trade", "performance", "analysis", "accounts", "risk", "audit", "pocket"];
const fromHash = (): Route => { const h = window.location.hash.replace("#", "").split("/")[0] as Route; return ROUTES.includes(h) ? h : "dashboard"; };

function Inner() {
  const { session, error, authError, logout, readOnly, health } = useStore();
  const [route, setRoute] = useState<Route>(fromHash);
  useEffect(() => { const f = () => setRoute(fromHash()); window.addEventListener("hashchange", f); return () => window.removeEventListener("hashchange", f); }, []);
  const go = (r: Route) => { window.location.hash = r; setRoute(r); };
  if (!session) return <Login />;
  if (route === "pocket") return <><LockOverlay /><Pocket onRoute={go} /></>;
  return (
    <Shell route={route} onRoute={go}>
      <LockOverlay />
      {health?.watchdog?.degraded && <div className="banner bad" data-testid="degraded-banner"><b>MODO DEGRADADO</b>: sin addon de NinjaTrader desde las {health.watchdog.degraded_since?.slice(11, 16)}. No se copia ni se puede operar o cerrar desde aquí hasta que vuelva. Revisa que NinjaTrader esté abierto, el addon cargado (F5) y la conexión con el bróker.</div>}
      {health?.watchdog?.no_ticks && !health.watchdog.degraded && <div className="banner warn" data-testid="noticks-banner"><b>Sin datos de mercado</b> con NinjaTrader conectado y el mercado abierto{health.watchdog.last_tick_age_s ? ` (último precio hace ${Math.round(health.watchdog.last_tick_age_s / 60)} min)` : ""}: revisa la conexión del proveedor de datos en NinjaTrader.</div>}
      {readOnly && <div className="banner warn" data-testid="readonly-banner">Modo <b>solo lectura</b>: has entrado con el token de lectura. Puedes ver todo, pero el engine rechazará cualquier orden, cierre o cambio de configuración.</div>}
      {authError && <div className="banner bad" data-testid="auth-error" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}><span>{authError}</span><button className="chip-btn" onClick={logout} style={{ marginLeft: "auto" }}>Salir y volver a entrar</button></div>}
      {error && !authError && <div className="banner bad">Sin conexión con el engine: {error}</div>}
      {route === "dashboard" && <Dashboard onRoute={go} />}
      {route === "trade" && <Trade />}
      {route === "performance" && <Performance />}
      {route === "analysis" && <Analysis />}
      {route === "accounts" && <Accounts />}
      {route === "risk" && <Risk />}
      {route === "audit" && <Audit />}
    </Shell>
  );
}

export default function App() { return <StoreProvider><Inner /></StoreProvider>; }
