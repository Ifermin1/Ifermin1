// Cliente HTTP + WebSocket del engine. La URL y el token se guardan en localStorage
// para que la PWA instalada en el teléfono recuerde a qué engine conectarse.

export type Health = {
  app: string; mode: "mock" | "ninja";
  bridge: { mode: string; connected: boolean; last_msg_in: string | null; last_msg_out: string | null;
            last_sync: string | null; last_heartbeat: string | null; master_account: string | null; error_count: number; master_feed_up: boolean; follower_feed_up: boolean; sync_up: boolean };
  risk: RiskState; stats: { events_in: number; orders_out: number; blocked: number; errors: number; rejected: number; fills: number; duplicates: number }; ws_clients: number;
};
export type Position = { account_id: string; symbol: string; quantity: number; avg_price: number; unrealized_pnl: number };
export type Account = { account_id: string; balance: number; net_liquidity: number; daily_pnl: number; open_positions: Position[]; updated_at: string };
export type Rule = { id: string; master_account: string; follower_account: string; multiplier: number;
                     symbol_filter: string | null; enabled: boolean };
export type AuditEvent = { id: number | null; timestamp: string; event_type: string; source_account: string | null;
                           target_account: string | null; message: string; details: Record<string, unknown> | null };
export type RiskLimit = { account_id: string; max_daily_loss: number; max_position_size: number; trading_halted: boolean };
export type RiskState = { kill_switch: boolean; kill_switch_reason: string | null; kill_switch_at: string | null; limits: RiskLimit[] };

export type Session = { baseUrl: string; token: string };

const KEY = "tpx.session";

export function loadSession(): Session | null {
  try { const raw = localStorage.getItem(KEY); return raw ? JSON.parse(raw) : null; } catch { return null; }
}
export function saveSession(s: Session | null) {
  try { s ? localStorage.setItem(KEY, JSON.stringify(s)) : localStorage.removeItem(KEY); } catch { /* modo privado */ }
}

export class ApiError extends Error { constructor(public status: number, msg: string) { super(msg); } }

export function makeClient(s: Session) {
  const base = s.baseUrl.replace(/\/$/, "");
  async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const r = await fetch(base + path, {
      ...init,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${s.token}`, ...(init.headers || {}) },
    });
    if (!r.ok) throw new ApiError(r.status, (await r.text()) || r.statusText);
    return r.status === 204 ? (undefined as T) : r.json();
  }
  return {
    health: () => req<Health>("/api/health"),
    accounts: () => req<Account[]>("/api/accounts"),
    rules: () => req<Rule[]>("/api/rules"),
    createRule: (b: Omit<Rule, "id">) => req<Rule>("/api/rules", { method: "POST", body: JSON.stringify(b) }),
    updateRule: (id: string, b: Partial<Rule>) => req<Rule>(`/api/rules/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
    deleteRule: (id: string) => req<void>(`/api/rules/${id}`, { method: "DELETE" }),
    link: (follower: string, master_account: string, multiplier: number, enabled: boolean) =>
      req<Rule>(`/api/accounts/${encodeURIComponent(follower)}/link`, { method: "PUT", body: JSON.stringify({ master_account, multiplier, enabled }) }),
    unlink: (follower: string, master: string) =>
      req<void>(`/api/accounts/${encodeURIComponent(follower)}/link?master_account=${encodeURIComponent(master)}`, { method: "DELETE" }),
    audit: (limit = 100, type?: string) => req<AuditEvent[]>(`/api/audit?limit=${limit}${type ? `&event_type=${type}` : ""}`),
    risk: () => req<RiskState>("/api/risk"),
    killSwitch: (active: boolean, reason?: string) =>
      req<RiskState>("/api/risk/kill-switch", { method: "POST", body: JSON.stringify({ active, reason }) }),
    upsertLimit: (l: RiskLimit) => req<RiskLimit>("/api/risk/limits", { method: "PUT", body: JSON.stringify(l) }),
    mockEvent: (b: Record<string, unknown> = {}) => req<unknown>("/api/mock/master-event", { method: "POST", body: JSON.stringify(b) }),
    wsUrl: () => `${base.replace(/^http/, "ws")}/api/ws?token=${encodeURIComponent(s.token)}`,
  };
}
export type Client = ReturnType<typeof makeClient>;
