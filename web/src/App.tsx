import { useEffect, useState } from "react";
import { StoreProvider, useStore } from "./lib/store";
import { Shell, type Route } from "./components/Shell";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { Performance } from "./pages/Performance";
import { Accounts } from "./pages/Accounts";
import { Replicator } from "./pages/Replicator";
import { Risk } from "./pages/Risk";
import { Audit } from "./pages/Audit";

const ROUTES: Route[] = ["dashboard", "performance", "accounts", "replicator", "risk", "audit"];
const fromHash = (): Route => { const h = window.location.hash.replace("#", "") as Route; return ROUTES.includes(h) ? h : "dashboard"; };

function Inner() {
  const { session, error } = useStore();
  const [route, setRoute] = useState<Route>(fromHash);
  useEffect(() => { const f = () => setRoute(fromHash()); window.addEventListener("hashchange", f); return () => window.removeEventListener("hashchange", f); }, []);
  const go = (r: Route) => { window.location.hash = r; setRoute(r); };
  if (!session) return <Login />;
  return (
    <Shell route={route} onRoute={go}>
      {error && <div className="banner bad">Sin conexión con el engine: {error}</div>}
      {route === "dashboard" && <Dashboard />}
      {route === "performance" && <Performance />}
      {route === "accounts" && <Accounts />}
      {route === "replicator" && <Replicator />}
      {route === "risk" && <Risk />}
      {route === "audit" && <Audit />}
    </Shell>
  );
}

export default function App() { return <StoreProvider><Inner /></StoreProvider>; }
