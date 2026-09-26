import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { show as notifyShow } from "./notify";
import { makeClient, loadSession, saveSession, type Account, type AuditEvent, type Client, type Health, type Price,
         type RiskState, type Rule, type Session } from "./api";
import { root } from "./format";

type WsStatus = "connecting" | "open" | "closed";
export type Density = "cozy" | "compact";
export type PriceMap = Record<string, Price>;

type Store = {
  session: Session | null; client: Client | null;
  login: (s: Session) => Promise<void>; logout: () => void;
  health: Health | null; accounts: Account[]; rules: Rule[]; audit: AuditEvent[]; risk: RiskState | null;
  prices: PriceMap; wsStatus: WsStatus; error: string | null;
  density: Density; setDensity: (d: Density) => void;
  theme: Theme; setTheme: (t: Theme) => void;
  refresh: () => Promise<void>; setRules: (r: Rule[]) => void; setRisk: (r: RiskState) => void;
};

const Ctx = createContext<Store | null>(null);
const DENSITY_KEY = "tpx.density";
export type Theme = "dark" | "light";
const THEME_KEY = "tpx.theme";
const loadTheme = (): Theme => { try { return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark"; } catch { return "dark"; } };
const loadDensity = (): Density => { try { return localStorage.getItem(DENSITY_KEY) === "compact" ? "compact" : "cozy"; } catch { return "cozy"; } };

export function StoreProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const client = useMemo(() => (session ? makeClient(session) : null), [session]);
  const [health, setHealth] = useState<Health | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [risk, setRisk] = useState<RiskState | null>(null);
  const [prices, setPrices] = useState<PriceMap>({});
  const [wsStatus, setWsStatus] = useState<WsStatus>("closed");
  const [error, setError] = useState<string | null>(null);
  const [density, setDensityState] = useState<Density>(loadDensity);
  const wsRef = useRef<WebSocket | null>(null);
  const priceBuf = useRef<PriceMap>({});   // los ticks llegan varias veces por segundo: se vuelcan al estado cada 400 ms

  const setDensity = useCallback((d: Density) => { setDensityState(d); try { localStorage.setItem(DENSITY_KEY, d); } catch { /* modo privado */ } }, []);
  useEffect(() => { document.documentElement.dataset.density = density; }, [density]);
  const [theme, setThemeState] = useState<Theme>(loadTheme);
  const setTheme = useCallback((t: Theme) => { setThemeState(t); try { localStorage.setItem(THEME_KEY, t); } catch { /* modo privado */ } }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#f5f7fb" : "#0b1220");
  }, [theme]);

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
        else if (topic === "audit.event") { setAudit((list) => [data, ...list].slice(0, 300)); void notifyShow(data); }
        else if (topic === "market.price" && data?.symbol && typeof data.last === "number") {
          priceBuf.current[root(data.symbol)] = { symbol: data.symbol, last: data.last, bid: data.bid ?? data.last, ask: data.ask ?? data.last, at: Date.now() };
        }
      };
      ws.onclose = () => { setWsStatus("closed"); window.clearInterval(ping); if (!stopped) timer = window.setTimeout(connect, 2000); };
      ws.onerror = () => ws.close();
    };
    void refresh(); connect();
    const poll = window.setInterval(() => void client.health().then(setHealth).catch(() => {}), 10000);
    const flush = window.setInterval(() => {
      const buf = priceBuf.current;
      if (Object.keys(buf).length === 0) return;
      priceBuf.current = {};
      setPrices((p) => ({ ...p, ...buf }));
    }, 400);
    return () => { stopped = true; window.clearTimeout(timer); window.clearInterval(ping); window.clearInterval(poll); window.clearInterval(flush); wsRef.current?.close(); };
  }, [client, refresh]);

  const value: Store = { session, client, login, logout, health, accounts, rules, audit, risk, prices, wsStatus, error,
                         density, setDensity, theme, setTheme, refresh, setRules, setRisk };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore fuera de StoreProvider");
  return s;
}
