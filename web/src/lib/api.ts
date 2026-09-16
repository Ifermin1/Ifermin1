// Cliente HTTP + WebSocket del engine. La URL y el token se guardan en localStorage
// para que la PWA instalada en el teléfono recuerde a qué engine conectarse.

export type Health = {
  app: string; mode: "mock" | "ninja"; addon_outdated: boolean; min_addon_version: string;
  bridge: { mode: string; connected: boolean; last_msg_in: string | null; last_msg_out: string | null;
            last_sync: string | null; last_heartbeat: string | null; master_account: string | null; addon_version: string | null; error_count: number; master_feed_up: boolean; follower_feed_up: boolean; sync_up: boolean };
  risk: RiskState; stats: { events_in: number; orders_out: number; blocked: number; errors: number; rejected: number; fills: number; duplicates: number;
           latency_ms_last: number | null; latency_ms_avg: number | null; slippage_last: number | null; slippage_avg: number | null;
           seq_gaps: number; addon_restarts: number; flattens: number }; ws_clients: number;
};
export type Position = { account_id: string; symbol: string; quantity: number; avg_price: number; unrealized_pnl: number };
export type WorkingOrder = { order_id: string; master_order_id: string; action: string; symbol: string; quantity: number; filled: number;
                             order_type: string; limit_price: number; stop_price: number; state: string };
export type Account = { account_id: string; balance: number; net_liquidity: number; daily_pnl: number; open_positions: Position[]; updated_at: string;
                        enabled: boolean; enabled_source: "auto" | "user"; alias: string; connected: boolean | null; connection: string; reported: boolean;
                        realized_pnl: number; unrealized_pnl: number; desync: boolean; desync_detail: string; working_orders: WorkingOrder[] };
/** Tick de precio del addon (topic market.price), indexado por raíz del símbolo (NQ, MNQ…). */
export type Price = { symbol: string; last: number; bid: number; ask: number; at: number };
export type PnlHistory = Record<string, [string, number][]>;
export type Rule = { id: string; master_account: string; follower_account: string; multiplier: number;
                     symbol_filter: string | null; enabled: boolean; target_root: string | null;
                     entry_mode: "market" | "limit"; tolerance_ticks: number; entry_timeout_s: number; entry_fallback: "market" | "cancel" };
export type ExecOptions = { target_root?: string | null; entry_mode?: "market" | "limit"; tolerance_ticks?: number; entry_timeout_s?: number; entry_fallback?: "market" | "cancel" };
export type AuditEvent = { id: number | null; timestamp: string; event_type: string; source_account: string | null;
                           target_account: string | null; message: string; details: Record<string, unknown> | null };
export type RiskLimit = { account_id: string; max_daily_loss: number; max_daily_profit: number; max_position_size: number; trading_halted: boolean; halted_reason: string; halted_at: string | null };
export type Schedule = { enabled: boolean; window_start: string; flatten_at: string; include_master: boolean; last_flatten_day: string };
export type RiskState = { kill_switch: boolean; kill_switch_reason: string | null; kill_switch_at: string | null; limits: RiskLimit[];
                          schedule: Schedule; session_closed: boolean; addon_silent: boolean };

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
    createRule: (b: Pick<Rule, "master_account" | "follower_account" | "multiplier" | "symbol_filter" | "enabled">) => req<Rule>("/api/rules", { method: "POST", body: JSON.stringify(b) }),
    updateRule: (id: string, b: Partial<Rule>) => req<Rule>(`/api/rules/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
    deleteRule: (id: string) => req<void>(`/api/rules/${id}`, { method: "DELETE" }),
    setAccount: (id: string, body: { enabled?: boolean; alias?: string; auto?: boolean }) =>
      req<Account>(`/api/accounts/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
    forgetAccount: (id: string) => req<void>(`/api/accounts/${encodeURIComponent(id)}`, { method: "DELETE" }),
    setMaster: (account: string) => req<{ master_account: string }>("/api/master", { method: "POST", body: JSON.stringify({ account }) }),
    link: (follower: string, master_account: string, multiplier: number, enabled: boolean, opts: ExecOptions = {}) =>
      req<Rule>(`/api/accounts/${encodeURIComponent(follower)}/link`, { method: "PUT", body: JSON.stringify({ master_account, multiplier, enabled, ...opts }) }),
    unlink: (follower: string, master: string) =>
      req<void>(`/api/accounts/${encodeURIComponent(follower)}/link?master_account=${encodeURIComponent(master)}`, { method: "DELETE" }),
    audit: (limit = 100, type?: string) => req<AuditEvent[]>(`/api/audit?limit=${limit}${type ? `&event_type=${type}` : ""}`),
    risk: () => req<RiskState>("/api/risk"),
    pnl: (hours = 24) => req<PnlHistory>(`/api/pnl?hours=${hours}`),
    killSwitch: (active: boolean, reason?: string, flatten = false, flatten_master = true) =>
      req<RiskState>("/api/risk/kill-switch", { method: "POST", body: JSON.stringify({ active, reason, flatten, flatten_master }) }),
    flattenAll: (include_master: boolean, reason?: string) =>
      req<{ results: Record<string, string> }>("/api/risk/flatten-all", { method: "POST", body: JSON.stringify({ include_master, reason }) }),
    flatten: (id: string, reason?: string) =>
      req<{ result: string }>(`/api/accounts/${encodeURIComponent(id)}/flatten`, { method: "POST", body: JSON.stringify({ reason }) }),
    resync: (id: string) =>
      req<{ sent: { symbol: string; action: string; quantity: number }[] }>(`/api/accounts/${encodeURIComponent(id)}/resync`, { method: "POST" }),
    upsertLimit: (l: Omit<RiskLimit, "halted_reason" | "halted_at">) => req<RiskLimit>("/api/risk/limits", { method: "PUT", body: JSON.stringify(l) }),
    deleteLimit: (id: string) => req<void>(`/api/risk/limits/${encodeURIComponent(id)}`, { method: "DELETE" }),
    setSchedule: (s: Omit<Schedule, "last_flatten_day">) => req<Schedule>("/api/risk/schedule", { method: "PUT", body: JSON.stringify(s) }),
    reopenSession: () => req<RiskState>("/api/risk/reopen", { method: "POST" }),
    mockEvent: (b: Record<string, unknown> = {}) => req<unknown>("/api/mock/master-event", { method: "POST", body: JSON.stringify(b) }),
    wsUrl: () => `${base.replace(/^http/, "ws")}/api/ws?token=${encodeURIComponent(s.token)}`,
  };
}
export type Client = ReturnType<typeof makeClient>;
