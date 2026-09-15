import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { makeClient, loadSession, saveSession, type Account, type AuditEvent, type Client, type Health,
         type RiskState, type Rule, type Session } from "./api";

type WsStatus = "connecting" | "open" | "closed";

type Store = {
  session: Session | null; client: Client | null;
  login: (s: Session) => Promise<void>; logout: () => void;
  health: Health | null; accounts: Account[]; rules: Rule[]; audit: AuditEvent[]; risk: RiskState | null;
  wsStatus: WsStatus; error: string | null;
  refresh: () => Promise<void>; setRules: (r: Rule[]) => void; setRisk: (r: RiskState) => void;
};

const Ctx = createContext<Store | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const client = useMemo(() => (session ? makeClient(session) : null), [session]);
  const [health, setHealth] = useState<Health | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [risk, setRisk] = useState<RiskState | null>(null);
  const [wsStatus, setWsStatus] = useState<WsStatus>("closed");
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const refresh = useCallback(async () => {
    if (!client) return;
    try {
      const [h, a, r, au, rk] = await Promise.all([client.health(), client.accounts(), client.rules(), client.audit(100), client.risk()]);
      setHealth(h); setAccounts(a); setRules(r); setAudit(au); setRisk(rk); setError(null);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }, [client]);

  const login = useCallback(async (s: Session) => {
    const c = makeClient(s);
    await c.health(); // valida URL + token antes de guardar
    saveSession(s); setSession(s);
  }, []);
  const logout = useCallback(() => { saveSession(null); setSession(null); wsRef.current?.close(); }, []);

  // WebSocket con reconexión
  useEffect(() => {
    if (!client) return;
    let stopped = false; let timer: number | undefined; let ping: number | undefined;
    const connect = () => {
      setWsStatus("connecting");
      const ws = new WebSocket(client.wsUrl());
      wsRef.current = ws;
      ws.onopen = () => { setWsStatus("open"); ping = window.setInterval(() => ws.readyState === 1 && ws.send("ping"), 25000); };
      ws.onmessage = (ev) => {
        const { topic, data } = JSON.parse(ev.data);
        if (topic === "accounts.snapshot") setAccounts(data);
        else if (topic === "broker.health") setHealth((h) => (h ? { ...h, bridge: data } : h));
        else if (topic === "risk.state") setRisk(data);
        else if (topic === "audit.event") setAudit((list) => [data, ...list].slice(0, 300));
      };
      ws.onclose = () => { setWsStatus("closed"); window.clearInterval(ping); if (!stopped) timer = window.setTimeout(connect, 2000); };
      ws.onerror = () => ws.close();
    };
    void refresh(); connect();
    const poll = window.setInterval(() => void client.health().then(setHealth).catch(() => {}), 10000);
    return () => { stopped = true; window.clearTimeout(timer); window.clearInterval(ping); window.clearInterval(poll); wsRef.current?.close(); };
  }, [client, refresh]);

  const value: Store = { session, client, login, logout, health, accounts, rules, audit, risk, wsStatus, error, refresh, setRules, setRisk };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore fuera de StoreProvider");
  return s;
}
