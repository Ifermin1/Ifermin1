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
/** Drawdown dinámico (trailing) como lo mide el prop firm: distancia entre el máximo que llegó a valer la cuenta y su valor actual. */
export type Drawdown = { equity: number; mode: "intraday" | "eod" | "closed"; peak: number; peak_at: string | null; drawdown: number; limit: number;
                         floor: number | null; room: number | null; pct: number | null; buffer: number; locked: boolean };
export type Account = { account_id: string; balance: number; net_liquidity: number; daily_pnl: number; contracts_today: number; commissions_today: number; net_pnl: number;
                        open_positions: Position[]; updated_at: string;
                        enabled: boolean; enabled_source: "auto" | "user"; alias: string; connected: boolean | null; connection: string; reported: boolean;
                        realized_pnl: number; unrealized_pnl: number; desync: boolean; desync_detail: string; working_orders: WorkingOrder[]; drawdown: Drawdown };
/** Tick de precio del addon (topic market.price), indexado por raíz del símbolo (NQ, MNQ…). */
export type Price = { symbol: string; last: number; bid: number; ask: number; at: number };
export type PnlHistory = Record<string, [string, number][]>;
export type Rule = { id: string; master_account: string; follower_account: string; multiplier: number;
                     symbol_filter: string | null; enabled: boolean; target_root: string | null;
                     entry_mode: "market" | "limit"; tolerance_ticks: number; entry_timeout_s: number; entry_fallback: "market" | "cancel" };
export type ExecOptions = { target_root?: string | null; entry_mode?: "market" | "limit"; tolerance_ticks?: number; entry_timeout_s?: number; entry_fallback?: "market" | "cancel" };
export type AuditEvent = { id: number | null; timestamp: string; event_type: string; source_account: string | null;
                           target_account: string | null; message: string; details: Record<string, unknown> | null };
export type RiskLimit = { account_id: string; max_daily_loss: number; max_daily_profit: number; max_position_size: number;
                          max_trailing_drawdown: number; drawdown_mode: "intraday" | "eod" | "closed"; drawdown_floor_cap: number; drawdown_buffer: number;
                          trading_halted: boolean; halted_reason: string; halted_at: string | null };
export type Schedule = { enabled: boolean; window_start: string; flatten_at: string; include_master: boolean; last_flatten_day: string };
export type Commissions = { enabled: boolean; default_per_side: number; rates: Record<string, number> };
export type RiskState = { kill_switch: boolean; kill_switch_reason: string | null; kill_switch_at: string | null; limits: RiskLimit[];
                          schedule: Schedule; session_closed: boolean; addon_silent: boolean; commissions: Commissions };

/** Calidad de ejecución: una orden del maestro copiada y el fill de cada seguidora (deslizamiento en ticks, positivo = peor). */
export type ExecFollower = { name: string; expected: number; filled: number; price: number | null; slip: number | null; slip_ticks: number | null;
                             latency_ms: number | null; broker_ms: number | null };
export type Execution = { order_id: string; at: string; symbol: string; action: string; order_type: string; kind: "entry" | "exit";
                          quantity: number; price: number; tick: number; followers: ExecFollower[] };
export type EntryPreset = { entry_mode: "market" | "limit"; tolerance_ticks?: number; entry_timeout_s?: number; entry_fallback?: "market" | "cancel" };

/** Calendario de rendimiento: un día (P&L del bróker si lo hay, si no la suma de operaciones) y las operaciones cerradas. */
export type DayStat = { day: string; pnl: number; net: number; pnl_broker: number | null; pnl_trades: number; trades: number; wins: number; losses: number;
                        best: number; worst: number; commissions: number; contracts: number;
                        accounts: Record<string, { pnl_trades: number; trades: number; pnl_broker: number | null }> };
export type Trade = { id: number; account_id: string; day: string; symbol: string; side: "long" | "short"; quantity: number; entry_price: number;
                      exit_price: number; pnl: number; commissions: number; opened_at: string | null; closed_at: string; fills: number };
export type MonthStats = { from: string; to: string; days: DayStat[]; trades: Trade[]; accounts: string[] };

/** Avisos por Telegram (engine → teléfono). */
export type NotifyState = { enabled: boolean; bot_token: string; chat_id: string; events: string[]; defaults: string[]; titles: Record<string, string>;
                            stats: { sent: number; errors: number; last_error: string | null; last_sent_at: number | null; suppressed: number } };
export type NotifyConfig = Pick<NotifyState, "enabled" | "bot_token" | "chat_id" | "events">;

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
    execution: (limit = 40) => req<Execution[]>(`/api/execution?limit=${limit}`),
    notifications: () => req<NotifyState>("/api/notifications"),
    setNotifications: (c: NotifyConfig) => req<NotifyState>("/api/notifications", { method: "PUT", body: JSON.stringify(c) }),
    testNotifications: () => req<{ ok: boolean; error: string | null }>("/api/notifications/test", { method: "POST" }),
    discoverChat: (bot_token?: string) => req<{ ok: boolean; chat_id?: string; name?: string; error?: string }>("/api/notifications/discover-chat", { method: "POST", body: JSON.stringify({ bot_token }) }),
    /** Calendario: `month` = AAAA-MM; `account` vacío = todas. */
    month: (month: string, account = "") => req<MonthStats>(`/api/stats/month?month=${month}${account ? `&account=${encodeURIComponent(account)}` : ""}`),
    /** Mismo modo de entrada para todas las seguidoras de la maestra actual. */
    setEntryForAll: (p: EntryPreset) => req<Rule[]>("/api/rules/entry", { method: "PUT", body: JSON.stringify(p) }),
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
    /** Fija el máximo del drawdown (marca de agua) de una cuenta; null = reiniciarlo al valor actual. */
    setPeak: (id: string, peak: number | null) => req<Account>(`/api/accounts/${encodeURIComponent(id)}/peak`, { method: "PUT", body: JSON.stringify({ peak }) }),
    setSchedule: (s: Omit<Schedule, "last_flatten_day">) => req<Schedule>("/api/risk/schedule", { method: "PUT", body: JSON.stringify(s) }),
    reopenSession: () => req<RiskState>("/api/risk/reopen", { method: "POST" }),
    setCommissions: (c: Commissions) => req<Commissions>("/api/risk/commissions", { method: "PUT", body: JSON.stringify(c) }),
    mockEvent: (b: Record<string, unknown> = {}) => req<unknown>("/api/mock/master-event", { method: "POST", body: JSON.stringify(b) }),
    wsUrl: () => `${base.replace(/^http/, "ws")}/api/ws?token=${encodeURIComponent(s.token)}`,
  };
}
export type Client = ReturnType<typeof makeClient>;
