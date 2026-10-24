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

const ROUTES: Route[] = ["dashboard", "trade", "performance", "analysis", "accounts", "risk", "audit", "pocket"];
const fromHash = (): Route => { const h = window.location.hash.replace("#", "").split("/")[0] as Route; return ROUTES.includes(h) ? h : "dashboard"; };

function Inner() {
  const { session, error, authError, logout } = useStore();
  const [route, setRoute] = useState<Route>(fromHash);
  useEffect(() => { const f = () => setRoute(fromHash()); window.addEventListener("hashchange", f); return () => window.removeEventListener("hashchange", f); }, []);
  const go = (r: Route) => { window.location.hash = r; setRoute(r); };
  if (!session) return <Login />;
  if (route === "pocket") return <Pocket onRoute={go} />;
  return (
    <Shell route={route} onRoute={go}>
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
