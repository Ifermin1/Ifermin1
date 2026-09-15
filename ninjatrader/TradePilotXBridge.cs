// TradePilot X — NinjaTrader 8 AddOn
// ---------------------------------------------------------------------------
// Puente ZeroMQ entre NinjaTrader y TradePilot X.
//
//   :5555 PUB  -> TradePilot SUB   eventos de la cuenta master (EXECUTION, ORDER_*),
//                                  ticks PRICE y HEARTBEAT
//   :5556 SUB  <- TradePilot PUB   órdenes para las cuentas follower
//   :5557 REP  <- TradePilot REQ   "GET_ACCOUNTS"     -> "Sim101|50000.0;Sim102|25000.0"  (conectadas)
//                                  "GET_ACCOUNTS_ALL" -> "Sim101|50000.0|Connected|MFF;..." (todas)
//
// Instalación: ver ninjatrader/README.md (requiere NetMQ.dll + AsyncIO.dll en
// Documents\NinjaTrader 8\bin\Custom y añadirlas como referencias).
//
// Configuración: Documents\NinjaTrader 8\TradePilotX\config.json (se crea con
// valores por defecto la primera vez que arranca el add-on).
// ---------------------------------------------------------------------------
#region Using declarations
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using NetMQ;
using NetMQ.Sockets;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.AddOns
{
    public class TradePilotXBridge : AddOnBase
    {
        private const string BridgeVersion = "1.1";

        // ---- configuración ------------------------------------------------
        private class BridgeConfig
        {
            public string MasterAccount = "Sim101";
            public string Host = "127.0.0.1";
            public int MasterPort = 5555;
            public int FollowerPort = 5556;
            public int SyncPort = 5557;
            public int PriceThrottleMs = 250;
            public int HeartbeatMs = 5000;
            public bool PublishPrices = true;
            public List<string> PriceInstruments = new List<string>();
            // Cuentas a reportar en GET_ACCOUNTS. Vacío = sólo las conectadas (+ la master).
            // Admite nombres exactos o prefijos terminados en * (p. ej. "Sim*", "APEX-112924-1*").
            // GET_ACCOUNTS_ALL ignora este filtro y devuelve todas las cuentas con su estado.
            public List<string> AccountFilter = new List<string>();
        }

        private static readonly string ConfigDir = Path.Combine(Core.Globals.UserDataDir, "TradePilotX");
        private static readonly string ConfigPath = Path.Combine(ConfigDir, "config.json");

        private BridgeConfig cfg = new BridgeConfig();

        // ---- ZMQ ----------------------------------------------------------
        private PublisherSocket pub;
        private SubscriberSocket sub;
        private ResponseSocket rep;
        private NetMQQueue<string> outbox;
        private NetMQPoller poller;
        private NetMQTimer priceTimer;
        private NetMQTimer heartbeatTimer;
        private volatile bool running;

        // ---- estado -------------------------------------------------------
        private Account master;
        private readonly ConcurrentDictionary<string, MarketData> marketFeeds = new ConcurrentDictionary<string, MarketData>();
        private readonly ConcurrentDictionary<string, double[]> latestPrices = new ConcurrentDictionary<string, double[]>(); // symbol -> [last,bid,ask]
        private readonly ConcurrentDictionary<string, bool> dirtyPrices = new ConcurrentDictionary<string, bool>();
        private readonly ConcurrentDictionary<string, DateTime> feedSubscribedAt = new ConcurrentDictionary<string, DateTime>();
        private readonly ConcurrentDictionary<string, bool> feedWarned = new ConcurrentDictionary<string, bool>();
        // (follower, master_order_id) -> orden del follower, para modificar/cancelar y evitar duplicados
        private readonly ConcurrentDictionary<string, Order> followerOrders = new ConcurrentDictionary<string, Order>();
        // Cuentas follower cuyas órdenes/ejecuciones ya escuchamos (ACK de vuelta a TradePilot)
        private readonly ConcurrentDictionary<string, Account> followers = new ConcurrentDictionary<string, Account>();
        private readonly object lifecycleLock = new object();

        // ===================================================================
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "TradePilot X Bridge";
                Description = "Puente ZeroMQ para el replicador y analizador TradePilot X";
            }
            else if (State == State.Configure)
            {
                Start();
            }
            else if (State == State.Terminated)
            {
                Stop();
            }
        }

        // ===================================================================
        // Arranque / parada
        // ===================================================================
        private void Start()
        {
            lock (lifecycleLock)
            {
                if (running) return;
                try
                {
                    cfg = LoadConfig();

                    outbox = new NetMQQueue<string>();
                    pub = new PublisherSocket();
                    pub.Options.SendHighWatermark = 10000;
                    pub.Options.Linger = TimeSpan.Zero;
                    pub.Bind(string.Format("tcp://{0}:{1}", cfg.Host, cfg.MasterPort));

                    sub = new SubscriberSocket();
                    sub.Options.Linger = TimeSpan.Zero;
                    sub.Bind(string.Format("tcp://{0}:{1}", cfg.Host, cfg.FollowerPort));
                    sub.SubscribeToAnyTopic();
                    sub.ReceiveReady += OnFollowerMessage;

                    rep = new ResponseSocket();
                    rep.Options.Linger = TimeSpan.Zero;
                    rep.Bind(string.Format("tcp://{0}:{1}", cfg.Host, cfg.SyncPort));
                    rep.ReceiveReady += OnSyncRequest;

                    outbox.ReceiveReady += (s, e) =>
                    {
                        string msg;
                        while (outbox.TryDequeue(out msg, TimeSpan.Zero))
                            pub.SendFrame(msg);
                    };

                    priceTimer = new NetMQTimer(TimeSpan.FromMilliseconds(Math.Max(50, cfg.PriceThrottleMs)));
                    priceTimer.Elapsed += (s, e) => FlushPrices();
                    heartbeatTimer = new NetMQTimer(TimeSpan.FromMilliseconds(Math.Max(1000, cfg.HeartbeatMs)));
                    heartbeatTimer.Elapsed += (s, e) =>
                    {
                        Publish(Json.Obj("msg_type", "HEARTBEAT", "account", cfg.MasterAccount, "version", BridgeVersion, "timestamp", Now()));
                        CheckSilentFeeds();
                    };

                    poller = new NetMQPoller { sub, rep, outbox, priceTimer, heartbeatTimer };
                    poller.RunAsync();
                    running = true;

                    Account.AccountStatusUpdate += OnAccountStatusUpdate;
                    AttachMaster();
                    foreach (string sym in cfg.PriceInstruments)
                        EnsurePriceFeed(sym);

                    Info(string.Format("Bridge v{0} online. master={1} pub={2} sub={3} sync={4} (GET_ACCOUNTS_ALL disponible)",
                        BridgeVersion, cfg.MasterAccount, cfg.MasterPort, cfg.FollowerPort, cfg.SyncPort));
                }
                catch (Exception ex)
                {
                    Error("Error arrancando el bridge: " + ex);
                    Stop();
                }
            }
        }

        private void Stop()
        {
            lock (lifecycleLock)
            {
                running = false;
                try { Account.AccountStatusUpdate -= OnAccountStatusUpdate; } catch { }
                DetachMaster();
                foreach (Account f in followers.Values)
                {
                    try { f.OrderUpdate -= OnFollowerOrder; f.ExecutionUpdate -= OnFollowerExecution; } catch { }
                }
                followers.Clear();
                foreach (var feed in marketFeeds.Values)
                {
                    try { feed.Update -= OnMarketData; } catch { }
                }
                marketFeeds.Clear();

                try { if (poller != null) { poller.Stop(); poller.Dispose(); } } catch { }
                foreach (IDisposable d in new IDisposable[] { sub, rep, pub, outbox })
                {
                    try { if (d != null) d.Dispose(); } catch { }
                }
                poller = null; sub = null; rep = null; pub = null; outbox = null; priceTimer = null; heartbeatTimer = null;
                try { NetMQConfig.Cleanup(false); } catch { }
                Info("Bridge offline.");
            }
        }

        // ===================================================================
        // Cuenta master: ejecuciones y órdenes
        // ===================================================================
        private void OnAccountStatusUpdate(object sender, AccountStatusEventArgs e)
        {
            // Las cuentas pueden aparecer/conectarse después de arrancar el add-on
            if (master == null && e.Account != null && e.Account.Name == cfg.MasterAccount)
                AttachMaster();
        }

        private void AttachMaster()
        {
            lock (lifecycleLock)
            {
                if (master != null) return; // evita suscribirse dos veces (eventos duplicados)
                lock (Account.All)
                    master = Account.All.FirstOrDefault(a => a.Name == cfg.MasterAccount);
                if (master == null)
                {
                    Warn("Cuenta master '" + cfg.MasterAccount + "' no encontrada todavía; esperando conexión.");
                    return;
                }
                master.ExecutionUpdate += OnMasterExecution;
                master.OrderUpdate += OnMasterOrder;
                master.PositionUpdate += OnMasterPosition;
                Info("Escuchando cuenta master " + master.Name);
            }
        }

        private void DetachMaster()
        {
            if (master == null) return;
            try
            {
                master.ExecutionUpdate -= OnMasterExecution;
                master.OrderUpdate -= OnMasterOrder;
                master.PositionUpdate -= OnMasterPosition;
            }
            catch { }
            master = null;
        }

        private void OnMasterExecution(object sender, ExecutionEventArgs e)
        {
            try
            {
                Order order = e.Execution != null ? e.Execution.Order : null;
                if (order == null || e.Quantity <= 0) return;
                string symbol = e.Execution.Instrument.FullName;
                EnsurePriceFeed(symbol);
                Publish(Json.Obj(
                    "msg_type", "EXECUTION",
                    "account", cfg.MasterAccount,
                    "action", ActionName(order.OrderAction),
                    "symbol", symbol,
                    "quantity", e.Quantity,
                    "price", e.Price,
                    "order_type", TypeName(order.OrderType),
                    "state", "Filled",
                    "order_id", OrderKey(order),
                    "execution_id", e.Execution.ExecutionId ?? "",
                    "timestamp", e.Time.ToString("o")));
            }
            catch (Exception ex) { Error("OnMasterExecution: " + ex.Message); }
        }

        private void OnMasterOrder(object sender, OrderEventArgs e)
        {
            try
            {
                Order order = e.Order;
                if (order == null) return;
                // Las órdenes a mercado se replican vía EXECUTION; aquí sólo el ciclo de vida de limitadas/stops
                if (order.OrderType == OrderType.Market) return;

                string msgType = null;
                switch (e.OrderState)
                {
                    case OrderState.Accepted:
                    case OrderState.Working:
                        msgType = order.Filled > 0 ? null : "ORDER_PENDING";
                        break;
                    case OrderState.ChangeSubmitted:
                        msgType = "ORDER_MODIFIED";
                        break;
                    case OrderState.Cancelled:
                        msgType = "ORDER_CANCELLED";
                        break;
                }
                if (msgType == null) return;
                // Un "Working" repetido tras un cambio ya se cubrió con ORDER_MODIFIED; TradePilot deduplica por (order_id, msg_type, qty)
                double price = order.OrderType == OrderType.Limit || order.OrderType == OrderType.StopLimit ? e.LimitPrice : e.StopPrice;
                Publish(Json.Obj(
                    "msg_type", msgType,
                    "account", cfg.MasterAccount,
                    "action", ActionName(order.OrderAction),
                    "symbol", order.Instrument.FullName,
                    "quantity", e.Quantity,
                    "price", price,
                    "limit_price", e.LimitPrice,
                    "stop_price", e.StopPrice,
                    "order_type", TypeName(order.OrderType),
                    "state", e.OrderState.ToString(),
                    "order_id", OrderKey(order),
                    "timestamp", e.Time.ToString("o")));
            }
            catch (Exception ex) { Error("OnMasterOrder: " + ex.Message); }
        }

        private void OnMasterPosition(object sender, PositionEventArgs e)
        {
            try
            {
                Publish(Json.Obj(
                    "msg_type", "POSITION",
                    "account", cfg.MasterAccount,
                    "symbol", e.Position.Instrument.FullName,
                    "market_position", e.MarketPosition.ToString(),
                    "quantity", e.Quantity,
                    "avg_price", e.AveragePrice,
                    "timestamp", Now()));
            }
            catch (Exception ex) { Error("OnMasterPosition: " + ex.Message); }
        }

        // ===================================================================
        // Precios
        // ===================================================================
        private readonly object feedLock = new object();

        private void EnsurePriceFeed(string symbol)
        {
            if (!running || !cfg.PublishPrices || string.IsNullOrWhiteSpace(symbol) || marketFeeds.ContainsKey(symbol)) return;
            lock (feedLock)
            try
            {
                Instrument instrument = Instrument.GetInstrument(symbol);
                if (instrument == null) { Warn("Instrumento no encontrado para precios: " + symbol); return; }
                // NinjaTrader puede nombrar el mismo contrato de dos formas ("ES 12-26" en config,
                // "ES DEC26" en ejecuciones); usamos su FullName como clave para no suscribir dos veces.
                string key = instrument.FullName ?? symbol;
                if (marketFeeds.ContainsKey(key)) return;
                MarketData feed = new MarketData(instrument);
                if (marketFeeds.TryAdd(key, feed))
                {
                    feed.Update += OnMarketData;
                    feedSubscribedAt[key] = DateTime.Now;
                    Info("Feed de precios activo: " + key);
                }
            }
            catch (Exception ex) { Error("EnsurePriceFeed(" + symbol + "): " + ex.Message); }
        }

        private void OnMarketData(object sender, MarketDataEventArgs e)
        {
            if (e.MarketDataType != MarketDataType.Last && e.MarketDataType != MarketDataType.Bid && e.MarketDataType != MarketDataType.Ask)
                return;
            MarketData feed = sender as MarketData;
            string symbol = e.Instrument.FullName;
            double[] cur = latestPrices.GetOrAdd(symbol, _ => new double[3]);
            lock (cur)
            {
                if (e.MarketDataType == MarketDataType.Last) cur[0] = e.Price;
                if (feed != null)
                {
                    if (feed.Bid != null && feed.Bid.Price > 0) cur[1] = feed.Bid.Price;
                    if (feed.Ask != null && feed.Ask.Price > 0) cur[2] = feed.Ask.Price;
                }
                if (e.MarketDataType == MarketDataType.Bid) cur[1] = e.Price;
                if (e.MarketDataType == MarketDataType.Ask) cur[2] = e.Price;
            }
            dirtyPrices[symbol] = true;
        }

        private void CheckSilentFeeds()
        {
            foreach (var kv in feedSubscribedAt)
            {
                if (latestPrices.ContainsKey(kv.Key) || feedWarned.ContainsKey(kv.Key)) continue;
                if ((DateTime.Now - kv.Value).TotalSeconds < 30) continue;
                feedWarned[kv.Key] = true;
                Warn("Sin ticks para " + kv.Key + " en 30 s: comprueba que el contrato está vigente (p. ej. 'ES 12-26') y que la conexión tiene datos de mercado.");
            }
        }

        private void FlushPrices()
        {
            foreach (string symbol in dirtyPrices.Keys.ToList())
            {
                bool dummy;
                dirtyPrices.TryRemove(symbol, out dummy);
                double[] cur;
                if (!latestPrices.TryGetValue(symbol, out cur)) continue;
                double last, bid, ask;
                lock (cur) { last = cur[0]; bid = cur[1]; ask = cur[2]; }
                if (last <= 0) continue;
                Publish(Json.Obj(
                    "msg_type", "PRICE", "symbol", symbol, "last", last,
                    "bid", bid > 0 ? (object)bid : null, "ask", ask > 0 ? (object)ask : null,
                    "timestamp", Now()));
            }
        }

        // ===================================================================
        // Follower: ejecución de órdenes que manda TradePilot
        // ===================================================================
        private void OnFollowerMessage(object sender, NetMQSocketEventArgs e)
        {
            string raw = null;
            try
            {
                raw = e.Socket.ReceiveFrameString();
                Dictionary<string, string> m = Json.ParseFlat(raw);
                string msgType = Get(m, "msg_type").ToUpperInvariant();
                string accountName = Get(m, "account");
                string symbol = Get(m, "symbol");
                string masterOrderId = Get(m, "master_order_id");
                int qty = (int)Num(m, "quantity");
                double price = Num(m, "price");

                Account account;
                lock (Account.All)
                    account = Account.All.FirstOrDefault(a => a.Name == accountName);
                if (account == null) { Warn("Follower desconocido: " + accountName + " | " + raw); return; }
                if (account.Name == cfg.MasterAccount) { Warn("Ignorada orden dirigida a la cuenta master (bucle): " + raw); return; }

                string key = accountName + "|" + masterOrderId;
                Order existing;
                followerOrders.TryGetValue(key, out existing);

                switch (msgType)
                {
                    case "EXECUTION":
                        // Si ya replicamos la orden limitada del master (ORDER_PENDING), su fill lo hará la propia orden del follower.
                        if (existing != null && IsWorking(existing)) { Info("EXECUTION ignorada: el follower ya tiene orden trabajando para " + masterOrderId); return; }
                        SubmitNew(account, symbol, Get(m, "action"), OrderType.Market, qty, 0, 0, key, masterOrderId);
                        break;

                    case "ORDER_PENDING":
                        if (existing != null && IsWorking(existing)) return; // duplicado
                        {
                            OrderType type = ParseType(Get(m, "order_type"));
                            double limit = NumOr(m, "limit_price", type == OrderType.Limit || type == OrderType.StopLimit ? price : 0);
                            double stop = NumOr(m, "stop_price", type == OrderType.StopMarket || type == OrderType.StopLimit ? price : 0);
                            if (type == OrderType.Market) type = OrderType.Limit;
                            SubmitNew(account, symbol, Get(m, "action"), type, qty, limit, stop, key, masterOrderId);
                        }
                        break;

                    case "ORDER_MODIFIED":
                        if (existing == null || !IsWorking(existing)) { Warn("ORDER_MODIFIED sin orden trabajando para " + key); return; }
                        {
                            double limit = NumOr(m, "limit_price", price);
                            double stop = NumOr(m, "stop_price", price);
                            if (qty > 0) existing.QuantityChanged = qty;
                            if (existing.OrderType == OrderType.Limit || existing.OrderType == OrderType.StopLimit) existing.LimitPriceChanged = limit;
                            if (existing.OrderType == OrderType.StopMarket || existing.OrderType == OrderType.StopLimit) existing.StopPriceChanged = stop;
                            account.Change(new[] { existing });
                            Info("Modificada " + key + " qty=" + qty + " limit=" + limit + " stop=" + stop);
                        }
                        break;

                    case "ORDER_CANCELLED":
                        if (existing == null || !IsWorking(existing)) { Info("ORDER_CANCELLED sin orden trabajando para " + key); return; }
                        account.Cancel(new[] { existing });
                        Info("Cancelada " + key);
                        break;

                    default:
                        Warn("msg_type desconocido desde TradePilot: " + msgType);
                        break;
                }
            }
            catch (Exception ex) { Error("OnFollowerMessage: " + ex.Message + " | " + raw); }
        }

        private void SubmitNew(Account account, string symbol, string action, OrderType type, int qty, double limit, double stop, string key, string masterOrderId)
        {
            Instrument instrument = Instrument.GetInstrument(symbol);
            if (instrument == null) { Warn("Instrumento desconocido: " + symbol); return; }
            if (qty <= 0) { Warn("Cantidad inválida para " + key); return; }
            AttachFollower(account);
            Order order = account.CreateOrder(instrument, ParseAction(action), type, OrderEntry.Manual, TimeInForce.Day,
                qty, limit, stop, string.Empty, "TPX " + masterOrderId, Core.Globals.MaxDate, null);
            followerOrders[key] = order;
            account.Submit(new[] { order });
            Info(string.Format("Enviada a {0}: {1} {2} {3} {4} (master #{5})", account.Name, action, qty, symbol, type, masterOrderId));
        }

        /// <summary>Con AccountFilter vacío: la master y las cuentas cuya conexión está activa.</summary>
        private bool IsReportable(Account a)
        {
            if (a.Name == cfg.MasterAccount) return true;
            if (cfg.AccountFilter.Count > 0)
            {
                foreach (string pattern in cfg.AccountFilter)
                {
                    if (pattern.EndsWith("*") ? a.Name.StartsWith(pattern.TrimEnd('*'), StringComparison.OrdinalIgnoreCase)
                                              : string.Equals(a.Name, pattern, StringComparison.OrdinalIgnoreCase))
                        return true;
                }
                return false;
            }
            try { return a.Connection != null && a.Connection.Status == ConnectionStatus.Connected; }
            catch { return true; }
        }

        /// <summary>Estado y nombre de la conexión de una cuenta, sin lanzar excepciones.</summary>
        private static void ConnectionInfo(Account a, out string status, out string connection)
        {
            status = "Disconnected"; connection = "";
            try
            {
                if (a.Connection != null)
                {
                    status = a.Connection.Status.ToString();
                    if (a.Connection.Options != null) connection = a.Connection.Options.Name ?? "";
                }
            }
            catch { }
        }

        // ---- ACK del follower: estado de órdenes y fills de vuelta a TradePilot ----
        private void AttachFollower(Account account)
        {
            if (followers.ContainsKey(account.Name)) return;
            if (followers.TryAdd(account.Name, account))
            {
                account.OrderUpdate += OnFollowerOrder;
                account.ExecutionUpdate += OnFollowerExecution;
                Info("Escuchando follower " + account.Name);
            }
        }

        private static string MasterIdFromName(string name)
        {
            return name != null && name.StartsWith("TPX ") ? name.Substring(4) : "";
        }

        private void OnFollowerOrder(object sender, OrderEventArgs e)
        {
            try
            {
                Order order = e.Order;
                if (order == null || !(order.Name ?? "").StartsWith("TPX ")) return; // sólo órdenes nuestras
                string account = order.Account != null ? order.Account.Name : "";
                string error = e.Error != Cbi.ErrorCode.NoError ? e.Error.ToString() : "";
                string native = e.Comment ?? "";
                string line = string.Format("Follower {0}: {1} {2} {3} {4} -> {5}{6}", account, order.Name, order.OrderAction,
                    order.Quantity, order.Instrument.FullName, e.OrderState, string.IsNullOrEmpty(error) ? "" : " ERROR=" + error + " " + native);
                if (e.OrderState == OrderState.Rejected) Error(line); else Info(line);
                Publish(Json.Obj(
                    "msg_type", "ORDER_STATUS",
                    "account", account,
                    "action", ActionName(order.OrderAction),
                    "symbol", order.Instrument.FullName,
                    "quantity", e.Quantity,
                    "filled", e.Filled,
                    "price", e.AverageFillPrice,
                    "limit_price", e.LimitPrice,
                    "stop_price", e.StopPrice,
                    "order_type", TypeName(order.OrderType),
                    "state", e.OrderState.ToString(),
                    "order_id", OrderKey(order),
                    "master_order_id", MasterIdFromName(order.Name),
                    "error", error,
                    "native_error", native,
                    "timestamp", e.Time.ToString("o")));
            }
            catch (Exception ex) { Error("OnFollowerOrder: " + ex.Message); }
        }

        private void OnFollowerExecution(object sender, ExecutionEventArgs e)
        {
            try
            {
                Order order = e.Execution != null ? e.Execution.Order : null;
                if (order == null || e.Quantity <= 0) return;
                string account = e.Execution.Account != null ? e.Execution.Account.Name : "";
                if (account == cfg.MasterAccount) return; // la master ya se publica por su propio handler
                Info(string.Format("Follower {0}: FILL {1} {2} {3} @ {4}", account, order.OrderAction, e.Quantity, e.Execution.Instrument.FullName, e.Price));
                Publish(Json.Obj(
                    "msg_type", "EXECUTION",
                    "account", account,
                    "action", ActionName(order.OrderAction),
                    "symbol", e.Execution.Instrument.FullName,
                    "quantity", e.Quantity,
                    "price", e.Price,
                    "order_type", TypeName(order.OrderType),
                    "state", "Filled",
                    "order_id", OrderKey(order),
                    "master_order_id", MasterIdFromName(order.Name),
                    "execution_id", e.Execution.ExecutionId ?? "",
                    "timestamp", e.Time.ToString("o")));
            }
            catch (Exception ex) { Error("OnFollowerExecution: " + ex.Message); }
        }

        private static bool IsWorking(Order o)
        {
            return o.OrderState == OrderState.Working || o.OrderState == OrderState.Accepted
                || o.OrderState == OrderState.Submitted || o.OrderState == OrderState.ChangeSubmitted
                || o.OrderState == OrderState.PartFilled;
        }

        // ===================================================================
        // Sync de cuentas (REQ/REP)
        // ===================================================================
        private void OnSyncRequest(object sender, NetMQSocketEventArgs e)
        {
            string request = null;
            try
            {
                request = e.Socket.ReceiveFrameString();
                string reply;
                if (request == "GET_ACCOUNTS")
                {
                    var parts = new List<string>();
                    lock (Account.All)
                    {
                        foreach (Account a in Account.All)
                        {
                            if (!IsReportable(a)) continue;
                            double cash = a.Get(AccountItem.CashValue, Currency.UsDollar);
                            parts.Add(a.Name + "|" + cash.ToString("0.00", CultureInfo.InvariantCulture));
                        }
                    }
                    reply = string.Join(";", parts);
                }
                else if (request == "GET_ACCOUNTS_ALL")
                {
                    // Todas las cuentas que NinjaTrader conoce, conectadas o no: "nombre|cash|estado|conexión"
                    var parts = new List<string>();
                    lock (Account.All)
                    {
                        foreach (Account a in Account.All)
                        {
                            double cash = 0;
                            try { cash = a.Get(AccountItem.CashValue, Currency.UsDollar); } catch { }
                            string status, connection;
                            ConnectionInfo(a, out status, out connection);
                            parts.Add(a.Name.Replace("|", "/").Replace(";", ",") + "|"
                                + cash.ToString("0.00", CultureInfo.InvariantCulture) + "|"
                                + status + "|" + connection.Replace("|", "/").Replace(";", ","));
                        }
                    }
                    reply = string.Join(";", parts);
                }
                else if (request == "PING")
                {
                    reply = "PONG";
                }
                else
                {
                    reply = "ERROR|unknown request";
                }
                e.Socket.SendFrame(reply);
            }
            catch (Exception ex)
            {
                Error("OnSyncRequest: " + ex.Message + " | " + request);
                try { e.Socket.SendFrame("ERROR|" + ex.Message); } catch { }
            }
        }

        // ===================================================================
        // Utilidades
        // ===================================================================
        private void Publish(string json)
        {
            if (!running || outbox == null) return;
            outbox.Enqueue(json);
        }

        private static string Now() { return DateTime.Now.ToString("o"); }

        private static string OrderKey(Order o)
        {
            if (!string.IsNullOrEmpty(o.OrderId)) return o.OrderId;
            return o.Name + "#" + o.Time.Ticks;
        }

        private static string ActionName(OrderAction a)
        {
            switch (a)
            {
                case OrderAction.Buy: return "BUY";
                case OrderAction.Sell: return "SELL";
                case OrderAction.SellShort: return "SELLSHORT";
                case OrderAction.BuyToCover: return "BUYTOCOVER";
                default: return a.ToString().ToUpperInvariant();
            }
        }

        private static OrderAction ParseAction(string s)
        {
            switch ((s ?? "").Trim().ToUpperInvariant().Replace("_", "").Replace(" ", ""))
            {
                case "BUY": return OrderAction.Buy;
                case "SELL": return OrderAction.Sell;
                case "SELLSHORT": return OrderAction.SellShort;
                case "BUYTOCOVER": return OrderAction.BuyToCover;
                default: throw new ArgumentException("Acción desconocida: " + s);
            }
        }

        private static string TypeName(OrderType t)
        {
            switch (t)
            {
                case OrderType.Market: return "MARKET";
                case OrderType.Limit: return "LIMIT";
                case OrderType.StopMarket: return "STOPMARKET";
                case OrderType.StopLimit: return "STOPLIMIT";
                case OrderType.MIT: return "MIT";
                default: return t.ToString().ToUpperInvariant();
            }
        }

        private static OrderType ParseType(string s)
        {
            switch ((s ?? "").Trim().ToUpperInvariant().Replace("_", "").Replace(" ", ""))
            {
                case "LIMIT": return OrderType.Limit;
                case "STOPMARKET": case "STOP": return OrderType.StopMarket;
                case "STOPLIMIT": return OrderType.StopLimit;
                case "MIT": return OrderType.MIT;
                default: return OrderType.Market;
            }
        }

        private static string Get(Dictionary<string, string> m, string key)
        {
            string v; return m.TryGetValue(key, out v) && v != null ? v : "";
        }

        private static double Num(Dictionary<string, string> m, string key)
        {
            double v; return double.TryParse(Get(m, key), NumberStyles.Float, CultureInfo.InvariantCulture, out v) ? v : 0;
        }

        private static double NumOr(Dictionary<string, string> m, string key, double fallback)
        {
            double v; return double.TryParse(Get(m, key), NumberStyles.Float, CultureInfo.InvariantCulture, out v) && v > 0 ? v : fallback;
        }

        private void Info(string msg) { NinjaTrader.Code.Output.Process("[TradePilotX] " + msg, PrintTo.OutputTab1); }
        private void Warn(string msg) { NinjaTrader.Code.Output.Process("[TradePilotX] WARN " + msg, PrintTo.OutputTab1); }
        private void Error(string msg)
        {
            NinjaTrader.Code.Output.Process("[TradePilotX] ERROR " + msg, PrintTo.OutputTab1);
            try { Log("[TradePilotX] " + msg, LogLevel.Error); } catch { }
        }

        // ---- config ------------------------------------------------------
        private BridgeConfig LoadConfig()
        {
            var c = new BridgeConfig();
            try
            {
                Directory.CreateDirectory(ConfigDir);
                if (!File.Exists(ConfigPath))
                {
                    File.WriteAllText(ConfigPath, DefaultConfigJson(c), new UTF8Encoding(false));
                    Info("Config creada con valores por defecto: " + ConfigPath);
                    return c;
                }
                Dictionary<string, string> m = Json.ParseFlat(File.ReadAllText(ConfigPath, Encoding.UTF8));
                if (m.ContainsKey("MasterAccount")) c.MasterAccount = m["MasterAccount"];
                if (m.ContainsKey("Host")) c.Host = m["Host"];
                if (m.ContainsKey("MasterPort")) c.MasterPort = (int)Num(m, "MasterPort");
                if (m.ContainsKey("FollowerPort")) c.FollowerPort = (int)Num(m, "FollowerPort");
                if (m.ContainsKey("SyncPort")) c.SyncPort = (int)Num(m, "SyncPort");
                if (m.ContainsKey("PriceThrottleMs")) c.PriceThrottleMs = (int)Num(m, "PriceThrottleMs");
                if (m.ContainsKey("HeartbeatMs")) c.HeartbeatMs = (int)Num(m, "HeartbeatMs");
                if (m.ContainsKey("PublishPrices")) c.PublishPrices = Get(m, "PublishPrices").ToLowerInvariant() != "false";
                if (m.ContainsKey("PriceInstruments"))
                    c.PriceInstruments = ParseList(Get(m, "PriceInstruments"));
                if (m.ContainsKey("AccountFilter"))
                    c.AccountFilter = ParseList(Get(m, "AccountFilter"));
                Info("Config cargada: " + ConfigPath);
            }
            catch (Exception ex) { Error("No se pudo leer config.json, usando defaults: " + ex.Message); }
            return c;
        }

        private static List<string> ParseList(string raw)
        {
            return (raw ?? "").Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(x => x.Trim().Trim('"')).Where(x => x.Length > 0).ToList();
        }

        private static string DefaultConfigJson(BridgeConfig c)
        {
            return "{\n"
                + "  \"MasterAccount\": \"" + c.MasterAccount + "\",\n"
                + "  \"Host\": \"" + c.Host + "\",\n"
                + "  \"MasterPort\": " + c.MasterPort + ",\n"
                + "  \"FollowerPort\": " + c.FollowerPort + ",\n"
                + "  \"SyncPort\": " + c.SyncPort + ",\n"
                + "  \"PublishPrices\": true,\n"
                + "  \"PriceThrottleMs\": " + c.PriceThrottleMs + ",\n"
                + "  \"HeartbeatMs\": " + c.HeartbeatMs + ",\n"
                + "  \"PriceInstruments\": [],\n"
                + "  \"AccountFilter\": []\n"
                + "}\n";
        }

        // ===================================================================
        // JSON mínimo (objetos planos) — evita dependencias extra en NinjaScript
        // ===================================================================
        private static class Json
        {
            public static string Obj(params object[] kv)
            {
                var sb = new StringBuilder("{");
                for (int i = 0; i + 1 < kv.Length; i += 2)
                {
                    if (i > 0) sb.Append(',');
                    sb.Append('"').Append(Escape(kv[i].ToString())).Append("\":").Append(Value(kv[i + 1]));
                }
                return sb.Append('}').ToString();
            }

            private static string Value(object v)
            {
                if (v == null) return "null";
                if (v is bool) return (bool)v ? "true" : "false";
                if (v is int || v is long) return Convert.ToString(v, CultureInfo.InvariantCulture);
                if (v is double || v is float || v is decimal)
                    return Convert.ToDouble(v).ToString("R", CultureInfo.InvariantCulture);
                return "\"" + Escape(v.ToString()) + "\"";
            }

            private static string Escape(string s)
            {
                return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "\\r").Replace("\t", "\\t");
            }

            private static readonly Regex Pair = new Regex(
                "\"(?<k>(?:[^\"\\\\]|\\\\.)*)\"\\s*:\\s*(?:\"(?<s>(?:[^\"\\\\]|\\\\.)*)\"|\\[(?<a>[^\\]]*)\\]|(?<v>[^,}\\s][^,}]*))",
                RegexOptions.Compiled);

            /// <summary>Parsea un objeto JSON plano: valores string, número, bool, null o array simple (devuelto como texto).</summary>
            public static Dictionary<string, string> ParseFlat(string json)
            {
                var d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                if (string.IsNullOrEmpty(json)) return d;
                foreach (Match m in Pair.Matches(json))
                {
                    string k = Unescape(m.Groups["k"].Value);
                    if (m.Groups["s"].Success) d[k] = Unescape(m.Groups["s"].Value);
                    else if (m.Groups["a"].Success) d[k] = m.Groups["a"].Value;
                    else
                    {
                        string v = m.Groups["v"].Value.Trim();
                        d[k] = v == "null" ? null : v;
                    }
                }
                return d;
            }

            private static string Unescape(string s)
            {
                return s.Replace("\\\"", "\"").Replace("\\n", "\n").Replace("\\r", "\r").Replace("\\t", "\t").Replace("\\\\", "\\");
            }
        }
    }
}
