import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { show as notifyShow } from "./notify";
import { ApiError, makeClient, loadSession, saveSession, type Account, type AuditEvent, type Client, type Health, type Price,
         type RiskState, type Rule, type Session } from "./api";
import { root } from "./format";
import { hashPin, loadLock, saveLock, type LockConfig } from "./lock";

type WsStatus = "connecting" | "open" | "closed";
export type Density = "cozy" | "compact";
export type PriceMap = Record<string, Price>;

type Store = {
  session: Session | null; client: Client | null;
  login: (s: Session) => Promise<void>; logout: () => void;
  health: Health | null; accounts: Account[]; rules: Rule[]; audit: AuditEvent[]; risk: RiskState | null;
  prices: PriceMap; wsStatus: WsStatus; error: string | null;
  wsRtt: number | null; wsLastAt: number;      // ida y vuelta del WebSocket (ms) y cuándo llegó el último mensaje del engine
  authError: string | null;                    // el engine rechazó el token guardado (401/4401) o bloqueó la IP (429)
  density: Density; setDensity: (d: Density) => void;
  theme: Theme; setTheme: (t: Theme) => void;
  refresh: () => Promise<void>; setRules: (r: Rule[]) => void; setRisk: (r: RiskState) => void;
  readOnly: boolean;                           // sesión con el token de solo lectura: el engine rechaza cualquier acción
  lock: { config: LockConfig; locked: boolean; asking: boolean };
  setLock: (c: LockConfig) => void;
  unlock: (pin: string | null) => Promise<boolean>;   // null = cancelar la petición de PIN
  guard: () => Promise<boolean>;               // antes de una acción con dinero: pide el PIN si está configurado; false = no seguir
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
  const [wsRtt, setWsRtt] = useState<number | null>(null);
  const [wsLastAt, setWsLastAt] = useState(0);
  const [wsStatus, setWsStatus] = useState<WsStatus>("closed");
  const [error, setError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const authRef = useRef<string | null>(null);
  const noteAuth = (e: unknown) => {
    const st = e instanceof ApiError ? e.status : 0;
    const msg = st === 401 ? "El engine rechazó el token guardado en este navegador (ha cambiado API_TOKEN en engine\\.env). Pulsa Salir y vuelve a entrar con el token actual."
      : st === 429 ? "El engine ha bloqueado esta IP unos minutos por demasiados tokens inválidos distintos. Espera 5 minutos y vuelve a entrar con el token correcto."
      : null;
    if (msg !== authRef.current) { authRef.current = msg; setAuthError(msg); }
    return !!msg;
  };
  const [density, setDensityState] = useState<Density>(loadDensity);
  // ---- candado (PIN local) ----
  const [lockCfg, setLockCfg] = useState<LockConfig>(loadLock);
  const [locked, setLocked] = useState(false);
  const [asking, setAsking] = useState(false);
  const askRef = useRef<((ok: boolean) => void) | null>(null);
  const lastActivity = useRef(Date.now());
  const lockRef = useRef(lockCfg); lockRef.current = lockCfg;
  const setLock = useCallback((c: LockConfig) => { setLockCfg(c); saveLock(c); if (!c.hash) setLocked(false); }, []);
  useEffect(() => {
    const touch = () => { lastActivity.current = Date.now(); };
    for (const ev of ["pointerdown", "keydown", "touchstart"]) window.addEventListener(ev, touch, { passive: true });
    const t = window.setInterval(() => {
      const c = lockRef.current;
      if (c.hash && c.minutes > 0 && Date.now() - lastActivity.current > c.minutes * 60000) setLocked(true);
    }, 5000);
    return () => { for (const ev of ["pointerdown", "keydown", "touchstart"]) window.removeEventListener(ev, touch); window.clearInterval(t); };
  }, []);
  const unlock = useCallback(async (pin: string | null) => {
    const c = lockRef.current;
    if (pin === null) { setAsking(false); askRef.current?.(false); askRef.current = null; return false; }
    const ok = !!c.hash && (await hashPin(pin, c.salt)) === c.hash;
    if (ok) { lastActivity.current = Date.now(); setLocked(false); setAsking(false); askRef.current?.(true); askRef.current = null; }
    return ok;
  }, []);
  const guard = useCallback(() => {
    const c = lockRef.current;
    if (!c.hash || !c.requirePin) return Promise.resolve(true);
    askRef.current?.(false);
    return new Promise<boolean>((resolve) => { askRef.current = resolve; setAsking(true); });
  }, []);
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
      setHealth(h); setAccounts(a); setRules(r); setAudit(au); setRisk(rk); setError(null); noteAuth(null);
    } catch (e) { if (!noteAuth(e)) setError(e instanceof Error ? e.message : String(e)); }
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
      ws.onopen = () => { setWsStatus("open"); ws.send(`ping:${Date.now()}`); ping = window.setInterval(() => ws.readyState === 1 && ws.send(`ping:${Date.now()}`), 10000); };
      ws.onmessage = (ev) => {
        const { topic, data } = JSON.parse(ev.data);
        setWsLastAt(Date.now());
        if (topic === "pong") { const t = Number(data?.t); if (t) setWsRtt(Math.max(0, Date.now() - t)); }
        else if (topic === "accounts.snapshot") setAccounts(data);
        else if (topic === "broker.health") setHealth((h) => (h ? { ...h, bridge: data } : h));
        else if (topic === "risk.state") setRisk(data);
        else if (topic === "audit.event") { setAudit((list) => [data, ...list].slice(0, 300)); void notifyShow(data); }
        else if (topic === "market.price" && data?.symbol && typeof data.last === "number") {
          priceBuf.current[root(data.symbol)] = { symbol: data.symbol, last: data.last, bid: data.bid ?? data.last, ask: data.ask ?? data.last, at: Date.now() };
        }
      };
      ws.onclose = (ev) => {
        setWsStatus("closed"); setWsRtt(null); window.clearInterval(ping);
        if (ev.code === 4401) noteAuth(new ApiError(401, "token"));
        // con el token rechazado se reintenta despacio: no tiene sentido insistir cada segundo con el mismo token
        if (!stopped) timer = window.setTimeout(connect, ev.code === 4401 || authRef.current ? 15000 : 1000);
      };
      ws.onerror = () => ws.close();
    };
    void refresh(); connect();
    const poll = window.setInterval(() => void client.health().then((h) => { setHealth(h); noteAuth(null); }).catch((e) => noteAuth(e)), 10000);
    const flush = window.setInterval(() => {
      const buf = priceBuf.current;
      if (Object.keys(buf).length === 0) return;
      priceBuf.current = {};
      setPrices((p) => ({ ...p, ...buf }));
    }, 400);
    return () => { stopped = true; window.clearTimeout(timer); window.clearInterval(ping); window.clearInterval(poll); window.clearInterval(flush); wsRef.current?.close(); };
  }, [client, refresh]);

  const value: Store = { session, client, login, logout, health, accounts, rules, audit, risk, prices, wsStatus, error, wsRtt, wsLastAt, authError,
                         density, setDensity, theme, setTheme, refresh, setRules, setRisk,
                         readOnly: health?.role === "read", lock: { config: lockCfg, locked, asking }, setLock, unlock, guard };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore fuera de StoreProvider");
  return s;
}
