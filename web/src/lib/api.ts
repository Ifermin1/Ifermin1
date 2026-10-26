// Cliente HTTP + WebSocket del engine. La URL y el token se guardan en localStorage
// para que la PWA instalada en el teléfono recuerde a qué engine conectarse.

export type Health = {
  app: string; mode: "mock" | "ninja"; addon_outdated: boolean; min_addon_version: string; web_build: string | null;
  public_url: string | null; token_weak: boolean; role?: "full" | "read"; read_token_set?: boolean;
  watchdog?: { degraded: boolean; degraded_since: string | null; no_ticks: boolean; market_open: boolean; last_tick_age_s: number | null; last_resume: { at: string; kind: string; gap_s: number } | null; loop_lag_ms: number } | null;
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
export type Drawdown = { equity: number; mode: "intraday" | "eod" | "closed" | "static"; peak: number; peak_at: string | null; drawdown: number; limit: number;
                         floor: number | null; room: number | null; pct: number | null; buffer: number; locked: boolean };
export type Account = { account_id: string; balance: number; net_liquidity: number; daily_pnl: number; contracts_today: number; commissions_today: number; net_pnl: number;
                        open_positions: Position[]; updated_at: string;
                        enabled: boolean; enabled_source: "auto" | "user"; alias: string; firm: string; plan: string; plan_size: number; tags: string[]; connected: boolean | null; connection: string; reported: boolean;
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
export type RiskLimit = { account_id: string; max_daily_loss: number; max_daily_profit: number; profit_goal: number; start_balance: number; max_position_size: number;
                          max_trailing_drawdown: number; drawdown_mode: "intraday" | "eod" | "closed" | "static"; drawdown_floor_cap: number; drawdown_buffer: number;
                          trading_halted: boolean; halted_reason: string; halted_at: string | null };
export type Schedule = { enabled: boolean; window_start: string; flatten_at: string; include_master: boolean; last_flatten_day: string };
export type Commissions = { enabled: boolean; default_per_side: number; rates: Record<string, number> };
export type RiskState = { kill_switch: boolean; kill_switch_reason: string | null; kill_switch_at: string | null; limits: RiskLimit[];
                          schedule: Schedule; session_closed: boolean; addon_silent: boolean; commissions: Commissions;
                          day_loss_limit: number; day_closed: boolean; day_total: number };
export type AutoRiskStatus = { enabled: boolean; firm: string; plan: string; max_contracts: number; buffer: number; pending: string[];
                               applied: { account_id: string; firm: string; plan: string; size: number; drawdown: number; drawdown_mode: string; buffer: number; contracts: number; target: number; daily_loss: number }[] };

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
export type HubState = { enabled: boolean; url: string; node: string; interval_s: number; queued: number; last_trade_id: number;
                         stats: { sent: number; batches: number; errors: number; last_error: string | null; last_ok_at: number | null; dropped: number } };
export type BackupState = { available: boolean; dir: string | null; keep: number; interval_h: number; integrity: string | null; last_at: string | null; last_error: string | null;
                            files: { name: string; size: number; at: string }[] };
export type PushState = { available: boolean; public_key: string; subscriptions: number; devices: { endpoint: string; ua: string; created_at: number | null }[];
                          stats: { sent: number; errors: number; removed: number; last_error: string | null; last_sent_at: number | null } };

/** Operativa manual (pestaña Operar). */
export type TrailSpec = { mode: "fixed" | "adaptive" | "ai"; distance_ticks?: number; step_ticks?: number; activate_ticks?: number; lock_be_ticks?: number; atr_k?: number; min_ticks?: number; max_ticks?: number };
export type ManualOrder = { client_id: string; account: string; symbol: string; action: "BUY" | "SELL"; quantity: number; order_type: "MARKET" | "LIMIT" | "STOPMARKET" | "STOPLIMIT";
                            limit_price?: number; stop_price?: number; sl_ticks?: number; tp_ticks?: number; note?: string; trail?: TrailSpec | null };
export type TradeTemplate = { id: string; name: string; symbol: string; quantity: number; order_type: ManualOrder["order_type"]; sl_ticks: number; tp_ticks: number; trail: TrailSpec | null };
export type OcoLeg = { action: "BUY" | "SELL"; order_type: "STOPMARKET" | "LIMIT" | "STOPLIMIT"; limit_price?: number; stop_price?: number };
export type Bars = { symbol: string; bars: { t: string; o: number; h: number; l: number; c: number; v: number; m: number }[]; last: number | null; age_s: number | null; atr_ticks: number | null; volume_ratio: number | null; n_bars: number };
export type ManualOrderResult = { client_id: string; order_id: string; status: string; detail: string; ms: number; account: string; symbol: string; action: string; quantity: number;
                                  order_type: string; at: string; duplicate?: boolean };
export type ProtectRequest = { client_id: string; account: string; symbol: string; breakeven?: boolean; be_offset_ticks?: number; sl_price?: number; tp_price?: number; last_price?: number; force?: boolean };
export type ProtectResult = { account: string; symbol: string; quantity: number; avg_price: number; ms: number;
                              sl: { action: "created" | "modified" | "unchanged"; order_id: string; price: number; from?: number } | null;
                              tp: { action: "created" | "modified" | "unchanged"; order_id: string; price: number; from?: number } | null };
export type TrailMode = "fixed" | "adaptive" | "ai";
export type TrailRequest = { account: string; symbol: string; mode: TrailMode; distance_ticks?: number; step_ticks?: number; activate_ticks?: number; lock_be_ticks?: number;
                             atr_k?: number; min_ticks?: number; max_ticks?: number };
export type Trail = { account: string; symbol: string; mode: TrailMode; distance_ticks: number; step_ticks: number; activate_ticks: number; lock_be_ticks: number; atr_k: number;
                      min_ticks: number; max_ticks: number; best: number | null; stop_price: number | null; stop_order_id: string | null; started_at: string; moves: number;
                      active: boolean; distance_now: number | null; ai_reason: string; ai_confidence: number | null; ai_error: string | null; ai_stop: number | null; note: string };
export type Trails = { trails: Trail[]; stats: { moves: number; ai_calls: number; ended: number }; ai_interval_s: number; ai: AiStatus | null;
                       market: Record<string, { symbol: string; last: number | null; age_s: number | null; atr_ticks: number | null; volume_ratio: number | null; bars: number }> };
export type RiskPreset = { id: string; name: string; multiplier: number; max_daily_loss: number; max_daily_profit: number; max_position_size: number; drawdown_buffer: number; entry_mode: "market" | "limit" };
export type AdviceAction = { type: "preset" | "pause" | "resume" | "link" | "resync" | "flatten" | "multiplier" | "profile"; label: string; preset_id?: string; value?: number };
export type Advice = { account_id: string; severity: "bad" | "warn" | "ok" | "info"; code: string; title: string; detail: string; actions: AdviceAction[] };
export type AdviceList = { advice: Advice[]; counts: Record<"bad" | "warn" | "ok" | "info", number>; generated_at: string };
export type ReviewResult = { day: string; source: "ia" | "regla"; model: string; ai_error?: string; generated_at: string;
                             stats: { n: number; wins: number; losses: number; win_rate: number | null; net: number; pf: number | null; max_loss_streak: number };
                             near_news: { trade: string; net: number; event: string; minutes_after: number }[]; quick_stops: string[];
                             review: { summary: string; patterns: { title: string; evidence: string; cost_usd: number | null }[]; tomorrow: string[]; score: number; mood: "bien" | "regular" | "mal" } };
export type AskResult = { question: string; answer: string; numbers: string[]; model: string | null; ms: number; context: { n_trades: number; period_days: number } };
export type EntryCheck = { verdict: "green" | "amber" | "red"; reasons: { level: "red" | "amber"; text: string }[]; checked_at: string; ai: { verdict?: string; reason?: string; model?: string; error?: string } | null };
export type ReconcileFinding = { kind: "unconfirmed_order" | "naked_position" | "pending_entry" | "desync" | "trail_without_position"; severity: "bad" | "warn" | "info";
                                 account: string; symbol: string; detail: string; action: "protect" | "resync" | "review"; client_id?: string; order_id?: string };
export type ReconcileReport = { ran_at: string | null; reason: string | null; findings: ReconcileFinding[]; counts: Record<string, number>; accounts: number };
export type StoredOrder = { client_id: string; account: string; symbol: string; action: string; quantity: number; order_type: string; status: string; order_id: string;
                            request: ManualOrder; result: { ms?: number; detail?: string; error?: string; status?: string }; created_at: string; updated_at: string };
export type TradeStatus = { ready: boolean; reason: string | null; max_qty: number; mode: string; symbols: string[]; addon_version: string | null; required_addon: string; ping_ms: number | null;
                            last_order_ms: number | null; heartbeat_age_s: number | null; master: string | null; trails: number; ai_enabled: boolean;
                            stats: { orders: number; rejected: number; cancels: number; modifies: number; unconfirmed: number } };

/** Calendario económico (ForexFactory vía el engine). */
export type NewsEvent = { id: string; time: string; country: string; country_name: string; title: string; impact: "high" | "medium" | "low" | "holiday";
                          forecast: string | null; previous: string | null; actual: string | null };
export type NewsAnalysis = { source: "ia" | "regla"; model: string; created_at: string;
                             data: { summary?: string; bias?: string; move?: string; advice?: string; surprise?: string; confidence?: number } };
export type NewsReaction = { symbol: string; px_before: number; px_5m: number | null; px_15m: number | null; px_30m: number | null; high: number | null; low: number | null };
export type NewsSurprise = { actual: number; forecast: number; diff: number; rel: number | null; tag: "mayor" | "menor" | "en línea" };
export type AiStatus = { enabled: boolean; model: string; last_error: string | null; calls: number };
export type NewsFeed = { events: (NewsEvent & { watched: boolean })[]; fetched_at: number | null; error: string | null; countries: string[]; source: string; refresh_s: number;
                         analyses: Record<string, { pre?: NewsAnalysis; post?: NewsAnalysis }>; reactions: Record<string, NewsReaction[]>; ai: AiStatus;
                         watch: { countries: string[]; min_impact: string; alert_minutes: number } };
export type NewsAlerts = { upcoming: { event: NewsEvent; minutes: number; analysis: NewsAnalysis | null }[]; next: { event: NewsEvent; minutes: number } | null;
                           results: { event: NewsEvent; minutes: number; analysis: NewsAnalysis | null; reaction: NewsReaction | null; surprise: NewsSurprise | null }[];
                           ai: AiStatus; window_min: number; countries: string[]; min_impact: string };
export type NewsBucket = { n: number; net: number; wins: number; win_rate: number | null; avg: number | null; pf: number | null; gross_profit: number; gross_loss: number };
export type NewsStats = { days: number; window_min: number; n_events: number; n_trades: number; trades: { near_high: NewsBucket; near_medium: NewsBucket; outside: NewsBucket };
                          by_title: (NewsBucket & { title: string })[];
                          reactions: Record<string, { n: number; avg_5m: number | null; avg_15m: number | null; avg_30m: number | null; avg_range: number | null; max_15m: number | null }>;
                          movers: { title: string; n: number; avg_15m: number | null }[]; follows_surprise: { agree: number; disagree: number };
                          surprises: { total: { n: number; mayor: number; menor: number; "en línea": number }; by_title: { title: string; n: number; beat: number; miss: number; inline: number; avg_abs_rel: number | null }[] } };

/** Análisis (pestaña Análisis): operaciones y filas por día de un periodo, movimientos de caja y parámetros. */
export type AnalysisDay = { day: string; trades: number; pnl_trades: number; pnl_broker: number | null; commissions: number; contracts: number;
                            balance: number | null; withdrawals: number; deposits: number };
export type CashEvent = { id: number; account_id: string; day: string; kind: "withdrawal" | "deposit" | "adjust"; amount: number; paid: number | null;
                          status: "pending" | "completed" | "rejected"; note: string; created_at: string };
export type CashEventInput = Omit<CashEvent, "id" | "created_at">;
export type AnalysisData = { from: string; to: string; trades: Trade[]; days: AnalysisDay[]; cash: CashEvent[]; accounts: string[]; fills: number };
export type AnalysisParams = { commission_rt: number; be_tolerance: number; loss_reference: number; short_seconds: number; start_balance: number | null;
                               use_engine_commissions: boolean };

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
  /** Descarga binaria/texto con el token en la cabecera (informes, CSV): devuelve el blob y el nombre sugerido. */
  async function raw(path: string): Promise<{ blob: Blob; filename: string }> {
    const r = await fetch(base + path, { headers: { Authorization: `Bearer ${s.token}` } });
    if (!r.ok) throw new ApiError(r.status, (await r.text()) || r.statusText);
    const m = /filename=([^;]+)/.exec(r.headers.get("Content-Disposition") ?? "");
    return { blob: await r.blob(), filename: m ? m[1].trim().replace(/"/g, "") : "descarga" };
  }
  return {
    health: () => req<Health>("/api/health"),
    accounts: () => req<Account[]>("/api/accounts"),
    rules: () => req<Rule[]>("/api/rules"),
    createRule: (b: Pick<Rule, "master_account" | "follower_account" | "multiplier" | "symbol_filter" | "enabled">) => req<Rule>("/api/rules", { method: "POST", body: JSON.stringify(b) }),
    updateRule: (id: string, b: Partial<Rule>) => req<Rule>(`/api/rules/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
    deleteRule: (id: string) => req<void>(`/api/rules/${id}`, { method: "DELETE" }),
    setAccount: (id: string, body: { enabled?: boolean; alias?: string; auto?: boolean; firm?: string; plan?: string; plan_size?: number; tags?: string[] }) =>
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
    backup: () => req<BackupState>("/api/backup"),
    backupRun: () => req<{ name: string; size: number; at: string; removed: number }>("/api/backup/run", { method: "POST" }),
    backupDownload: (name: string) => raw(`/api/backup/download?name=${encodeURIComponent(name)}`),
    configExport: () => raw("/api/config/export"),
    configImport: (data: unknown, replace_rules = false) => req<{ rules: number; risk_limits: number; accounts: number; other: string[] }>("/api/config/import", { method: "POST", body: JSON.stringify({ data, replace_rules }) }),
    hub: () => req<HubState>("/api/hub"),
    hubFlush: () => req<HubState & { ok: boolean; sent: number; error: string | null }>("/api/hub/flush", { method: "POST" }),
    pushState: () => req<PushState>("/api/push"),
    pushSubscribe: (subscription: Record<string, unknown>, ua: string) => req<PushState>("/api/push/subscribe", { method: "POST", body: JSON.stringify({ subscription, ua }) }),
    pushUnsubscribe: (endpoint: string) => req<PushState>("/api/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint }) }),
    pushTest: () => req<{ ok: boolean; sent: number; removed: number; error: string | null }>("/api/push/test", { method: "POST" }),
    testNotifications: () => req<{ ok: boolean; error: string | null }>("/api/notifications/test", { method: "POST" }),
    discoverChat: (bot_token?: string) => req<{ ok: boolean; chat_id?: string; name?: string; error?: string }>("/api/notifications/discover-chat", { method: "POST", body: JSON.stringify({ bot_token }) }),
    /** Análisis de un periodo: `from`/`to` = AAAA-MM-DD; `account` vacío = todas. */
    weeklyReport: (day: string, account = "", tag = "") => raw(`/api/report/weekly?day=${day}&account=${encodeURIComponent(account)}&tag=${encodeURIComponent(tag)}`),
    tradesCsv: (from: string, to: string, account = "", tag = "") => raw(`/api/trades.csv?day_from=${from}&day_to=${to}&account=${encodeURIComponent(account)}&tag=${encodeURIComponent(tag)}`),
    analysis: (from: string, to: string, account = "") =>
      req<AnalysisData>(`/api/stats/analysis?day_from=${from}&day_to=${to}${account ? `&account=${encodeURIComponent(account)}` : ""}`),
    cash: (account = "") => req<CashEvent[]>(`/api/cash${account ? `?account=${encodeURIComponent(account)}` : ""}`),
    addCash: (b: CashEventInput) => req<CashEvent>("/api/cash", { method: "POST", body: JSON.stringify(b) }),
    updateCash: (id: number, b: Partial<CashEventInput>) => req<CashEvent>(`/api/cash/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
    deleteCash: (id: number) => req<void>(`/api/cash/${id}`, { method: "DELETE" }),
    analysisParams: () => req<AnalysisParams>("/api/ui/analysis"),
    saveAnalysisParams: (p: AnalysisParams) => req<AnalysisParams>("/api/ui/analysis", { method: "PUT", body: JSON.stringify(p) }),
    news: (days = 7, countries = "", minImpact = "low") => req<NewsFeed>(`/api/news?days=${days}${countries ? `&countries=${encodeURIComponent(countries)}` : ""}&min_impact=${minImpact}`),
    tradeStatus: () => req<TradeStatus>("/api/trade/status"),
    tradeOrders: (limit = 50) => req<StoredOrder[]>(`/api/trade/orders?limit=${limit}`),
    reconcile: () => req<ReconcileReport>("/api/reconcile"),
    reconcileRun: () => req<ReconcileReport>("/api/reconcile/run", { method: "POST" }),
    tradeOrder: (o: ManualOrder) => req<ManualOrderResult>("/api/trade/order", { method: "POST", body: JSON.stringify(o) }),
    tradeCancel: (account: string, order_id: string) => req<{ ms: number; detail: string }>("/api/trade/cancel", { method: "POST", body: JSON.stringify({ account, order_id }) }),
    tradeModify: (account: string, order_id: string, quantity: number, limit_price: number, stop_price: number) =>
      req<{ ms: number; detail: string }>("/api/trade/modify", { method: "POST", body: JSON.stringify({ account, order_id, quantity, limit_price, stop_price }) }),
    trailStart: (r: TrailRequest) => req<Trail>("/api/trade/trail", { method: "POST", body: JSON.stringify(r) }),
    trailStop: (account: string, symbol: string) => req<Trail>(`/api/trade/trail?account=${encodeURIComponent(account)}&symbol=${encodeURIComponent(symbol)}`, { method: "DELETE" }),
    trails: () => req<Trails>("/api/trade/trails"),
    tradeProtect: (r: ProtectRequest) => req<ProtectResult>("/api/trade/protect", { method: "POST", body: JSON.stringify(r) }),
    tradeClose: (client_id: string, account: string, symbol: string, quantity = 0) => req<ManualOrderResult & { remaining?: number; cancelled_exits?: number }>("/api/trade/close", { method: "POST", body: JSON.stringify({ client_id, account, symbol, quantity }) }),
    tradeOco: (r: { client_id: string; account: string; symbol: string; quantity: number; legs: OcoLeg[]; sl_ticks?: number; tp_ticks?: number; trail?: TrailSpec | null }) =>
      req<{ client_id: string; legs: ManualOrderResult[]; trail_pending?: boolean }>("/api/trade/oco", { method: "POST", body: JSON.stringify(r) }),
    templates: () => req<{ templates: TradeTemplate[] }>("/api/ui/trade-templates"),
    saveTemplates: (templates: TradeTemplate[]) => req<{ templates: TradeTemplate[] }>("/api/ui/trade-templates", { method: "PUT", body: JSON.stringify({ templates }) }),
    bars: (symbol: string, n = 60) => req<Bars>(`/api/market/bars?symbol=${encodeURIComponent(symbol)}&n=${n}`),
    newsAlerts: () => req<NewsAlerts>("/api/news/alerts"),
    newsStats: (days = 90) => req<NewsStats>(`/api/news/stats?days=${days}`),
    newsAnalyze: (id: string, kind: "pre" | "post", force = false) => req<{ event: NewsEvent; analysis: NewsAnalysis; ai: AiStatus }>(`/api/news/analyze/${encodeURIComponent(id)}?kind=${kind}${force ? "&force=true" : ""}`, { method: "POST" }),
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
    setTags: (accounts: string[], add: string[], remove: string[]) => req<Account[]>("/api/accounts/tags", { method: "PUT", body: JSON.stringify({ accounts, add, remove }) }),
    presets: () => req<{ presets: RiskPreset[]; defaults: RiskPreset[] }>("/api/ui/risk-presets"),
    savePresets: (presets: RiskPreset[]) => req<{ presets: RiskPreset[]; defaults: RiskPreset[] }>("/api/ui/risk-presets", { method: "PUT", body: JSON.stringify({ presets }) }),
    applyPreset: (preset_id: string, accounts: string[]) => req<{ applied: string[]; preset: RiskPreset }>("/api/risk/preset", { method: "POST", body: JSON.stringify({ preset_id, accounts }) }),
    advice: () => req<AdviceList>("/api/advice"),
    aiReview: (day = "", force = false) => req<ReviewResult>("/api/ai/review", { method: "POST", body: JSON.stringify({ day, force }) }),
    aiAsk: (question: string, days = 90) => req<AskResult>("/api/ai/ask", { method: "POST", body: JSON.stringify({ question, days }) }),
    aiCheckEntry: (o: { account: string; symbol: string; action: string; quantity: number; order_type: string; sl_ticks: number; tp_ticks: number }, ai = false) =>
      req<EntryCheck>("/api/ai/check-entry", { method: "POST", body: JSON.stringify({ ...o, ai }) }),
    setDayLoss: (limit: number) => req<RiskState>("/api/risk/day-loss", { method: "PUT", body: JSON.stringify({ limit }) }),
    autoRiskStatus: () => req<AutoRiskStatus>("/api/risk/auto"),
    autoRisk: (account_id = "", force = false) => req<{ applied: AutoRiskStatus["applied"]; skipped: number }>("/api/risk/auto", { method: "POST", body: JSON.stringify({ account_id, force }) }),
    setSchedule: (s: Omit<Schedule, "last_flatten_day">) => req<Schedule>("/api/risk/schedule", { method: "PUT", body: JSON.stringify(s) }),
    reopenSession: () => req<RiskState>("/api/risk/reopen", { method: "POST" }),
    setCommissions: (c: Commissions) => req<Commissions>("/api/risk/commissions", { method: "PUT", body: JSON.stringify(c) }),
    mockEvent: (b: Record<string, unknown> = {}) => req<unknown>("/api/mock/master-event", { method: "POST", body: JSON.stringify(b) }),
    wsUrl: () => `${base.replace(/^http/, "ws")}/api/ws?token=${encodeURIComponent(s.token)}`,
  };
}
export type Client = ReturnType<typeof makeClient>;
