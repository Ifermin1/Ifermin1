// TradePilot X — NinjaTrader 8 AddOn
// ---------------------------------------------------------------------------
// Puente ZeroMQ entre NinjaTrader y TradePilot X.
//
//   :5555 PUB  -> TradePilot SUB   eventos de la cuenta master (EXECUTION, ORDER_*),
//                                  ticks PRICE y HEARTBEAT
//   :5556 SUB  <- TradePilot PUB   órdenes para las cuentas follower
//   :5557 REP  <- TradePilot REQ   "GET_ACCOUNTS"     -> "Sim101|50000.0;Sim102|25000.0"  (conectadas)
//                                  "GET_ACCOUNTS_ALL" -> "Sim101|50000.0|Connected|MFF;..." (todas)
//                                  "GET_MASTER"       -> "Sim101"
//                                  "SET_MASTER|Sim102" -> "OK|Sim102"  (cambia la master en caliente y la guarda)
//                                  "GET_POSITIONS"    -> "Sim101|NQ SEP26|Long|2|28936.0;..."  (todas las cuentas)
//                                  "GET_ORDERS"       -> "acc|orderKey|masterId|action|symbol|qty|filled|type|limit|stop|state;..." (v2.2)
//                                  "FLATTEN|Sim102"   -> "OK|Sim102|2"  (cancela órdenes y cierra posiciones)
//                                  "WATCH|Sim102"     -> "OK|Sim102"  (escuchar órdenes/posiciones de un follower)
//                                  "PING"             -> "PONG|<boot>|<seq>" (v1.8: arranque del addon y último seq publicado)
//   v2.0: EXECUTION del master lleva is_exit / is_entry (Execution.IsExit / IsEntry del bróker).
//   v2.3: un fill PARCIAL del master ajusta la copia en proporción (no la cancela entera); ORDER_MODIFIED sin copia viva
//         recrea el stop/TP si el follower aún tiene posición que proteger; EXECUTION lleva order_filled / order_quantity.
//                                  "ORDER|{json}"     -> "OK|tipo" / "IGNORED|motivo" / "ERROR|motivo" (v1.9: orden con confirmación;
//                                                        mismo JSON que por 5556, que sigue aceptándose para engines antiguos)
//   Todos los mensajes publicados llevan "seq" creciente para detectar pérdidas.
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
        private const string BridgeVersion = "2.4";

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
        // Órdenes pendientes del master ya publicadas: NinjaTrader emite Accepted y Working seguidos
        private readonly ConcurrentDictionary<string, bool> pendingPublished = new ConcurrentDictionary<string, bool>();
        // Cantidad ya copiada a mercado para cubrir lo que la orden del follower NO ejecutó (rechazo/cancelación)
        private readonly ConcurrentDictionary<string, int> reconciledQty = new ConcurrentDictionary<string, int>();
        // v2.0: fills del master pendientes de reconciliar mientras se cancela la copia viva del follower (clave -> qty)
        private readonly ConcurrentDictionary<string, int> reconcileRequested = new ConcurrentDictionary<string, int>();
        // v2.1: copias canceladas por un FLATTEN: un fill posterior del master en esa orden no debe reabrir posición
        private readonly ConcurrentDictionary<string, bool> flattenedKeys = new ConcurrentDictionary<string, bool>();
        // v2.4: doble salida. Cuando una copia parcialmente ejecutada se cancela para mandar el resto a mercado, NinjaTrader
        // puede reportar "Cancelled" y acto seguido llenarla igualmente (16/9 14:30: Sim102 vendió 3 con 2 comprados y quedó
        // corta). Se guarda cuánto llevaba ejecutado al reconciliar y la orden a mercado enviada; si la original ejecuta más
        // después, se cancela la de mercado si aún está viva y, si no, se deshace el exceso al instante.
        private readonly ConcurrentDictionary<string, int> filledAtReconcile = new ConcurrentDictionary<string, int>();
        private readonly ConcurrentDictionary<string, Order> reconcileOrders = new ConcurrentDictionary<string, Order>();
        private readonly ConcurrentDictionary<string, Order> reconciledOriginals = new ConcurrentDictionary<string, Order>();
        private readonly ConcurrentDictionary<string, int> fixedQty = new ConcurrentDictionary<string, int>();
        private readonly ConcurrentDictionary<string, bool> reconcileCancelAsked = new ConcurrentDictionary<string, bool>();
        private readonly ConcurrentDictionary<string, bool> phantomHandled = new ConcurrentDictionary<string, bool>();
        private DateTime lastPhantomSweep = DateTime.MinValue;
        // Claves (follower|master_order_id) cuya orden en el follower es copia de una orden PENDIENTE del master
        // (stop / take profit): sus fills los hace la propia orden del follower. Una entrada a mercado NO está aquí:
        // cada fill parcial del master se copia por separado.
        private readonly ConcurrentDictionary<string, bool> pendingCopies = new ConcurrentDictionary<string, bool>();
        // Entradas límite con tolerancia: si no se llenan a tiempo, a mercado lo que falte (o cancelar)
        private class EntryWatch { public Order Order; public DateTime Deadline; public string Fallback; public Account Account; public string Symbol; public string Action; public string Key; public string MasterId; }
        private readonly ConcurrentDictionary<string, EntryWatch> entryWatches = new ConcurrentDictionary<string, EntryWatch>();
        // Cuentas follower cuyas órdenes/ejecuciones ya escuchamos (ACK de vuelta a TradePilot)
        private readonly ConcurrentDictionary<string, Account> followers = new ConcurrentDictionary<string, Account>();
        private readonly object lifecycleLock = new object();
        private long seq;
        // id único de cada arranque del bridge: el engine lo compara entre el canal de eventos y el de comandos
        // para detectar en segundos que se reinició el addon y sus eventos ya no le llegan
        private string bootId = "";

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
                    priceTimer.Elapsed += (s, e) => { FlushPrices(); CheckEntryTimeouts(); SweepPhantoms(); };
                    heartbeatTimer = new NetMQTimer(TimeSpan.FromMilliseconds(Math.Max(1000, cfg.HeartbeatMs)));
                    heartbeatTimer.Elapsed += (s, e) =>
                    {
                        Publish(Json.Obj("msg_type", "HEARTBEAT", "account", cfg.MasterAccount, "version", BridgeVersion, "boot", bootId, "timestamp", Now()));
                        CheckSilentFeeds();
                    };

                    poller = new NetMQPoller { sub, rep, outbox, priceTimer, heartbeatTimer };
                    poller.RunAsync();
                    running = true;

                    bootId = Guid.NewGuid().ToString("N").Substring(0, 8);
                    Account.AccountStatusUpdate += OnAccountStatusUpdate;
                    AttachMaster();
                    RebuildState();
                    foreach (string sym in cfg.PriceInstruments)
                        EnsurePriceFeed(sym);

                    Info(string.Format("Bridge v{0} online. master={1} pub={2} sub={3} sync={4} (v2.4: doble salida corregida al instante y copias fantasma barridas)",
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
                    try { f.OrderUpdate -= OnFollowerOrder; f.ExecutionUpdate -= OnFollowerExecution; f.PositionUpdate -= OnFollowerPosition; } catch { }
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

        /// <summary>Cambia la cuenta master en caliente (desde TradePilot) y lo persiste en config.json.</summary>
        private string SetMaster(string name)
        {
            name = (name ?? "").Trim();
            if (name.Length == 0) return "ERROR|nombre vacío";
            bool exists;
            lock (Account.All) exists = Account.All.Any(a => a.Name == name);
            if (!exists) return "ERROR|cuenta desconocida: " + name;
            lock (lifecycleLock)
            {
                if (name == cfg.MasterAccount && master != null) return "OK|" + name;
                DetachMaster();
                cfg.MasterAccount = name;
                SaveConfig();
                AttachMaster();
                Publish(Json.Obj("msg_type", "HEARTBEAT", "account", cfg.MasterAccount, "version", BridgeVersion, "boot", bootId, "timestamp", Now()));
                Info("Cuenta master cambiada a " + name + (master == null ? " (aún no conectada)" : ""));
            }
            return "OK|" + name;
        }

        /// <summary>Tras un reinicio del addon, recupera las órdenes TPX vivas de los followers y las pendientes
        /// del master para no volver a copiarlas ni a publicarlas.</summary>
        private void RebuildState()
        {
            int recovered = 0;
            try
            {
                List<Account> all;
                lock (Account.All) all = Account.All.ToList();
                foreach (Account a in all)
                {
                    List<Order> orders;
                    lock (a.Orders) orders = a.Orders.ToList();
                    foreach (Order o in orders)
                    {
                        if (!IsLive(o)) continue;
                        if (a.Name == cfg.MasterAccount)
                        {
                            if (o.OrderType != OrderType.Market) pendingPublished[OrderKey(o)] = true;
                        }
                        else if ((o.Name ?? "").StartsWith("TPX "))
                        {
                            string k = a.Name + "|" + MasterIdFromName(o.Name);
                            followerOrders[k] = o;
                            if (o.OrderType != OrderType.Market) pendingCopies[k] = true;
                            AttachFollower(a);
                            recovered++;
                        }
                    }
                }
            }
            catch (Exception ex) { Error("RebuildState: " + ex.Message); }
            if (recovered > 0) Info("Estado recuperado: " + recovered + " órdenes TPX vivas en followers");
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
                    "is_exit", e.Execution.IsExit,
                    "is_entry", e.Execution.IsEntry,
                    "order_filled", order.Filled,
                    "order_quantity", order.Quantity,
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
                string orderKey = OrderKey(order);
                switch (e.OrderState)
                {
                    case OrderState.Accepted:
                    case OrderState.Working:
                        // Sólo la primera vez: Accepted -> Working (y Working tras un cambio) es la misma orden
                        msgType = order.Filled > 0 || !pendingPublished.TryAdd(orderKey, true) ? null : "ORDER_PENDING";
                        break;
                    case OrderState.ChangeSubmitted:
                        msgType = "ORDER_MODIFIED";
                        break;
                    case OrderState.Cancelled:
                        msgType = "ORDER_CANCELLED";
                        goto case OrderState.Rejected;
                    case OrderState.Filled:
                    case OrderState.Rejected:
                        bool dummy; pendingPublished.TryRemove(orderKey, out dummy);
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
            try { raw = e.Socket.ReceiveFrameString(); }
            catch (Exception ex) { Error("OnFollowerMessage: " + ex.Message); return; }
            HandleFollowerMessage(raw);
        }

        /// <summary>Orden de TradePilot. Llega por 5556 (PUB/SUB, sin confirmación) o, desde v1.9, como "ORDER|json"
        /// por 5557 (REQ/REP): así el engine sabe con certeza si la orden llegó. Devuelve "OK|...", "IGNORED|..." o "ERROR|...".</summary>
        private string HandleFollowerMessage(string raw)
        {
            try
            {
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
                if (account == null) { Warn("Follower desconocido: " + accountName + " | " + raw); return "ERROR|follower desconocido: " + accountName; }
                if (account.Name == cfg.MasterAccount) { Warn("Ignorada orden dirigida a la cuenta master (bucle): " + raw); return "IGNORED|orden dirigida a la maestra"; }

                string key = accountName + "|" + masterOrderId;
                Order existing;
                followerOrders.TryGetValue(key, out existing);

                switch (msgType)
                {
                    case "EXECUTION":
                        // Entrada a mercado del master: cada fill (parcial) llega como EXECUTION distinta y se copia tal cual.
                        if (existing == null || !pendingCopies.ContainsKey(key))
                        {
                            string entryKey = key + "#" + Get(m, "execution_id");
                            if (Get(m, "entry_mode").ToLowerInvariant() == "limit" && price > 0)
                                SubmitLimitEntry(account, symbol, Get(m, "action"), qty, price, (int)Num(m, "tolerance_ticks"),
                                    (int)NumOr(m, "entry_timeout_s", 5), Get(m, "entry_fallback"), entryKey, masterOrderId);
                            else
                                SubmitNew(account, symbol, Get(m, "action"), OrderType.Market, qty, 0, 0, entryKey, masterOrderId);
                            return "OK|EXECUTION";
                        }
                        // Copia de una orden PENDIENTE del master (stop / TP): su fill lo hace la propia orden del follower.
                        // Da igual que llegue antes o después: si la orden del follower está viva o ya ejecutó, no se copia.
                        {
                            if (IsLive(existing))
                            {
                                // v2.3: si el fill del master es PARCIAL (su orden sigue viva con resto), la copia NO se cancela:
                                // se reduce a la misma proporción y sólo la diferencia se cierra a mercado. Incidente 16/9 13:28:
                                // el stop del master ejecutó 1 de 2, la copia entera se canceló y el follower quedó sin protección.
                                Order mo = FindMasterOrder(masterOrderId);
                                if (mo != null && IsLive(mo) && mo.Filled < mo.Quantity)
                                {
                                    int masterRemaining = mo.Quantity - mo.Filled;
                                    int desiredRemaining = (int)Math.Round((double)masterRemaining * existing.Quantity / Math.Max(1, mo.Quantity));
                                    int copyRemaining = existing.Quantity - existing.Filled;
                                    int shortfall = copyRemaining - desiredRemaining;
                                    if (shortfall <= 0)
                                    {
                                        Info("Fill parcial del master en " + masterOrderId + " (" + mo.Filled + "/" + mo.Quantity + "): la copia va igual o por delante ("
                                            + existing.Filled + "/" + existing.Quantity + "), nada que hacer");
                                        return "IGNORED|fill parcial del master: la copia ya ejecutó lo suyo";
                                    }
                                    if (desiredRemaining > 0)
                                    {
                                        try
                                        {
                                            existing.QuantityChanged = existing.Filled + desiredRemaining;
                                            existing.LimitPriceChanged = existing.LimitPrice;
                                            existing.StopPriceChanged = existing.StopPrice;
                                            account.Change(new[] { existing });
                                        }
                                        catch (Exception cx) { Warn("No se pudo reducir la copia " + key + ": " + cx.Message); }
                                        Warn(string.Format("Fill parcial del master en {0} ({1}/{2}): copia reducida a {3} y {4} a mercado para seguirle",
                                            masterOrderId, mo.Filled, mo.Quantity, existing.Filled + desiredRemaining, shortfall));
                                        SubmitNew(account, symbol, Get(m, "action"), OrderType.Market, shortfall, 0, 0, key + "#part" + mo.Filled, masterOrderId);
                                        return "OK|EXECUTION_PARTIAL";
                                    }
                                }
                                // v2.0: el master ya ejecutó (su stop/TP saltó) pero la copia del follower sigue viva (otro precio,
                                // cola, cambio rechazado...). Cancelamos la copia y, cuando el bróker confirme la cancelación,
                                // cerramos a mercado lo que quedó sin ejecutar (ReconcileAfterCancel). Nunca antes: así no hay doble salida.
                                reconcileRequested.AddOrUpdate(key, qty, (k, v) => v + qty);
                                try { account.Cancel(new[] { existing }); }
                                catch (Exception cx) { Warn("No se pudo cancelar la copia viva " + key + ": " + cx.Message); }
                                Info("Fill del master en " + masterOrderId + " con la copia del follower aún viva: cancelando y cerrando a mercado lo pendiente");
                                return "OK|EXECUTION_RECONCILE";
                            }
                            if (flattenedKeys.ContainsKey(key))
                            {
                                // v2.1: la copia se canceló en un cierre de emergencia (posición ya cerrada): el fill del master
                                // en esa orden no se copia, o reabriría posición (incidente 16/9 12:14).
                                Info("EXECUTION ignorada: la copia de " + masterOrderId + " se canceló en un cierre de emergencia");
                                return "IGNORED|copia cancelada por cierre de emergencia";
                            }
                            int remaining = existing.Quantity - existing.Filled;
                            if (remaining <= 0) { Info("EXECUTION ignorada: la orden del follower ya se ejecutó (" + masterOrderId + ")"); return "IGNORED|la orden del follower ya se ejecutó"; }
                            // La orden del follower quedó sin ejecutar del todo (rechazada / cancelada): copiamos el fill del
                            // master a mercado, pero nunca más de lo que faltó por ejecutar.
                            int already = reconciledQty.GetOrAdd(key, 0);
                            int toSend = Math.Min(qty, remaining - already);
                            if (toSend <= 0) { Info("EXECUTION ignorada: lo pendiente de " + masterOrderId + " ya se cubrió"); return "IGNORED|lo pendiente ya se cubrió"; }
                            reconciledQty[key] = already + toSend;
                            Warn(string.Format("Orden del follower {0} quedó {1} con {2}/{3} ejecutados: copiando {4} a mercado",
                                masterOrderId, existing.OrderState, existing.Filled, existing.Quantity, toSend));
                            SubmitNew(account, symbol, Get(m, "action"), OrderType.Market, toSend, 0, 0, key + "#recon" + already, masterOrderId);
                            return "OK|EXECUTION_RECON";
                        }

                    case "ORDER_PENDING":
                        if (existing != null && IsLive(existing)) { Info("ORDER_PENDING duplicada ignorada para " + key); return "IGNORED|ORDER_PENDING duplicada"; }
                        {
                            OrderType type = ParseType(Get(m, "order_type"));
                            double limit = NumOr(m, "limit_price", type == OrderType.Limit || type == OrderType.StopLimit ? price : 0);
                            double stop = NumOr(m, "stop_price", type == OrderType.StopMarket || type == OrderType.StopLimit ? price : 0);
                            if (type == OrderType.Market) type = OrderType.Limit;
                            pendingCopies[key] = true;
                            SubmitNew(account, symbol, Get(m, "action"), type, qty, limit, stop, key, masterOrderId);
                        }
                        break;

                    case "ORDER_MODIFIED":
                        if (existing == null || !IsLive(existing))
                        {
                            // v2.3: la copia ya no existe (cancelada al reconciliar, rechazada...) pero el master sigue moviendo su
                            // stop/TP: si el follower aún tiene posición que proteger, se recrea con los nuevos parámetros y nunca
                            // por más de esa posición (TradePilot ya la recorta a lo que queda por proteger).
                            int pos = SignedPosition(account, symbol);
                            bool selling = Get(m, "action").ToUpperInvariant().StartsWith("SELL");
                            int protectable = selling ? Math.Max(0, pos) : Math.Max(0, -pos);
                            if (protectable <= 0)
                            {
                                Warn("ORDER_MODIFIED sin orden trabajando para " + key + " y sin posición que proteger: ignorada");
                                return "IGNORED|sin orden trabajando";
                            }
                            OrderType rtype = ParseType(Get(m, "order_type"));
                            double rlimit = NumOr(m, "limit_price", rtype == OrderType.Limit || rtype == OrderType.StopLimit ? price : 0);
                            double rstop = NumOr(m, "stop_price", rtype == OrderType.StopMarket || rtype == OrderType.StopLimit ? price : 0);
                            if (rtype == OrderType.Market) rtype = OrderType.Limit;
                            int rqty = Math.Min(qty, protectable);
                            pendingCopies[key] = true;
                            Warn("ORDER_MODIFIED sin copia viva para " + key + ": se recrea " + rqty + " (posición " + pos + ")");
                            SubmitNew(account, symbol, Get(m, "action"), rtype, rqty, rlimit, rstop, key, masterOrderId);
                            return "OK|ORDER_MODIFIED_RECREATED";
                        }
                        if (!IsWorking(existing)) { Warn("ORDER_MODIFIED con copia en estado " + existing.OrderState + " para " + key + ": ignorada"); return "IGNORED|sin orden trabajando"; }
                        {
                            double limit = NumOr(m, "limit_price", price);
                            double stop = NumOr(m, "stop_price", price);
                            // v2.4: NinjaTrader aplica los tres campos *Changed en Change(): se rellenan siempre, nunca con 0 contratos
                            existing.QuantityChanged = qty > 0 ? qty : existing.Quantity;
                            existing.LimitPriceChanged = (existing.OrderType == OrderType.Limit || existing.OrderType == OrderType.StopLimit) ? limit : existing.LimitPrice;
                            existing.StopPriceChanged = (existing.OrderType == OrderType.StopMarket || existing.OrderType == OrderType.StopLimit) ? stop : existing.StopPrice;
                            account.Change(new[] { existing });
                            Info("Modificada " + key + " qty=" + qty + " limit=" + limit + " stop=" + stop);
                        }
                        break;

                    case "ORDER_CANCELLED":
                        if (existing == null || !IsLive(existing)) { Info("ORDER_CANCELLED sin orden viva para " + key); return "IGNORED|sin orden viva"; }
                        try { account.Cancel(new[] { existing }); Info("Cancelada " + key); }
                        catch (Exception cx) { Warn("No se pudo cancelar " + key + " en estado " + existing.OrderState + ": " + cx.Message); }
                        break;

                    default:
                        Warn("msg_type desconocido desde TradePilot: " + msgType);
                        return "ERROR|msg_type desconocido: " + msgType;
                }
                return "OK|" + msgType;
            }
            catch (Exception ex) { Error("OnFollowerMessage: " + ex.Message + " | " + raw); return "ERROR|" + ex.Message; }
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

        /// <summary>Entrada como límite al precio del master +/- tolerancia (en ticks del instrumento del follower).
        /// Si no se llena en entry_timeout_s: fallback "market" manda a mercado lo que falte; "cancel" la cancela.</summary>
        private void SubmitLimitEntry(Account account, string symbol, string action, int qty, double refPrice, int ticks,
            int timeoutS, string fallback, string key, string masterOrderId)
        {
            Instrument instrument = Instrument.GetInstrument(symbol);
            if (instrument == null) { Warn("Instrumento desconocido: " + symbol); return; }
            if (qty <= 0) { Warn("Cantidad inválida para " + key); return; }
            OrderAction oa = ParseAction(action);
            bool buying = oa == OrderAction.Buy || oa == OrderAction.BuyToCover;
            double tick = instrument.MasterInstrument.TickSize;
            double limit = instrument.MasterInstrument.RoundToTickSize(refPrice + (buying ? 1 : -1) * ticks * tick);
            AttachFollower(account);
            Order order = account.CreateOrder(instrument, oa, OrderType.Limit, OrderEntry.Manual, TimeInForce.Day,
                qty, limit, 0, string.Empty, "TPX " + masterOrderId, Core.Globals.MaxDate, null);
            followerOrders[key] = order;
            account.Submit(new[] { order });
            entryWatches[key] = new EntryWatch { Order = order, Deadline = DateTime.Now.AddSeconds(Math.Max(1, timeoutS)),
                Fallback = (fallback ?? "market").ToLowerInvariant(), Account = account, Symbol = symbol, Action = action, Key = key, MasterId = masterOrderId };
            Info(string.Format("Entrada límite a {0}: {1} {2} {3} @ {4} (master {5} ±{6} ticks, {7} s, luego {8})",
                account.Name, action, qty, symbol, limit, refPrice, ticks, timeoutS, fallback));
        }

        private void CheckEntryTimeouts()
        {
            if (entryWatches.IsEmpty) return;
            DateTime now = DateTime.Now;
            foreach (var kv in entryWatches.ToList())
            {
                EntryWatch w = kv.Value;
                Order o = w.Order;
                if (!IsLive(o) || o.OrderState == OrderState.Filled)
                {
                    EntryWatch dummy; entryWatches.TryRemove(kv.Key, out dummy);
                    continue;
                }
                if (now < w.Deadline) continue;
                EntryWatch gone; entryWatches.TryRemove(kv.Key, out gone);
                int remaining = o.Quantity - o.Filled;
                try
                {
                    if (IsWorking(o)) w.Account.Cancel(new[] { o });
                    if (w.Fallback == "market" && remaining > 0)
                    {
                        Info(string.Format("Entrada límite {0} sin llenar ({1}/{2}) tras el plazo: {3} a mercado", w.Key, o.Filled, o.Quantity, remaining));
                        SubmitNew(w.Account, w.Symbol, w.Action, OrderType.Market, remaining, 0, 0, w.Key + "#mkt", w.MasterId);
                    }
                    else
                    {
                        Warn(string.Format("Entrada límite {0} cancelada sin llenar ({1}/{2}): la seguidora NO tiene esa entrada", w.Key, o.Filled, o.Quantity));
                        Publish(Json.Obj("msg_type", "ENTRY_MISSED", "account", w.Account.Name, "symbol", w.Symbol, "action", w.Action,
                            "quantity", remaining, "master_order_id", w.MasterId, "timestamp", Now()));
                    }
                }
                catch (Exception ex) { Error("CheckEntryTimeouts: " + ex.Message); }
            }
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
                account.PositionUpdate += OnFollowerPosition;
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
                if (e.OrderState == OrderState.Cancelled || e.OrderState == OrderState.Rejected || e.OrderState == OrderState.Filled)
                {
                    ReconcileAfterCancel(order, account);
                    // v2.4: la orden a mercado de una reconciliación llegó a su estado final: ¿quedó exceso que deshacer?
                    foreach (var kv in reconcileOrders.ToList())
                        if (ReferenceEquals(kv.Value, order)) SettleExcess(kv.Key);
                }
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

        /// <summary>v2.0: la copia viva se canceló porque el master ya ejecutó esa orden: cerrar a mercado lo que quedó
        /// sin ejecutar, nunca más de lo pedido ni de lo que faltaba. Si la copia se ejecutó antes de cancelarse, nada.</summary>
        private void ReconcileAfterCancel(Order order, string accountName)
        {
            string masterId = MasterIdFromName(order.Name);
            string key = accountName + "|" + masterId;
            int requested;
            if (!reconcileRequested.TryRemove(key, out requested)) return;
            if (flattenedKeys.ContainsKey(key)) { Info("Copia " + key + " cancelada por cierre de emergencia: nada que reconciliar"); return; }
            if (order.OrderState == OrderState.Filled) { Info("Copia " + key + " se ejecutó antes de cancelarse: nada que reconciliar"); return; }
            int remaining = order.Quantity - order.Filled;
            int already = reconciledQty.GetOrAdd(key, 0);
            int toSend = Math.Min(requested, remaining - already);
            if (toSend <= 0) return;
            reconciledQty[key] = already + toSend;
            Warn(string.Format("Copia {0} cancelada con {1}/{2} ejecutados: cerrando {3} a mercado para seguir al master",
                masterId, order.Filled, order.Quantity, toSend));
            SubmitNew(order.Account, order.Instrument.FullName, ActionName(order.OrderAction), OrderType.Market, toSend, 0, 0,
                key + "#recon" + already, masterId);
            // v2.4: si la original ejecuta más después de "cancelarse", habrá que deshacerlo (ver SettleExcess)
            filledAtReconcile[key] = order.Filled;
            reconciledOriginals[key] = order;
            Order sent;
            if (followerOrders.TryGetValue(key + "#recon" + already, out sent)) reconcileOrders[key] = sent;
        }

        /// <summary>v2.4: la copia original ejecutó DESPUÉS de reconciliarse (NinjaTrader la dio por cancelada y la llenó igual).
        /// Exceso = lo ejecutado de más por la original + lo ejecutado por la orden de mercado - lo que esa orden pedía.
        /// Si la de mercado sigue viva se cancela; lo que ya no se pueda evitar se deshace a mercado al instante.</summary>
        private void SettleExcess(string key)
        {
            Order original, recon;
            if (!reconciledOriginals.TryGetValue(key, out original)) return;
            int at; filledAtReconcile.TryGetValue(key, out at);
            int extra = original.Filled - at;
            if (extra <= 0) return;
            reconcileOrders.TryGetValue(key, out recon);
            if (recon != null && IsLive(recon) && recon.OrderState != OrderState.Filled)
            {
                if (reconcileCancelAsked.TryAdd(key, true))
                {
                    Warn(string.Format("Doble salida en {0}: la copia ejecutó {1} más después de cancelarse; cancelando la orden a mercado ({2}) antes de que se llene",
                        key, extra, recon.Quantity));
                    try { recon.Account.Cancel(new[] { recon }); } catch (Exception cx) { Warn("No se pudo cancelar la orden de reconciliación " + key + ": " + cx.Message); }
                }
                return;   // se vuelve a evaluar cuando la orden a mercado llegue a su estado final
            }
            int reconFilled = recon != null && recon.OrderState != OrderState.Rejected ? recon.Filled : 0;
            int reconWanted = recon != null ? recon.Quantity : 0;
            int fixedSoFar = fixedQty.GetOrAdd(key, 0);
            int over = extra + reconFilled - reconWanted - fixedSoFar;
            if (over <= 0) return;
            fixedQty[key] = fixedSoFar + over;
            bool wasSelling = original.OrderAction == OrderAction.Sell || original.OrderAction == OrderAction.SellShort;
            string undo = wasSelling ? "BUYTOCOVER" : "SELL";
            Error(string.Format("DOBLE SALIDA en {0}: la copia ejecutó {1} después de cancelarse y la reconciliación ya cerró {2} de {3}: deshaciendo {4} a mercado ({5})",
                key, extra, reconFilled, reconWanted, over, undo));
            SubmitNew(original.Account, original.Instrument.FullName, undo, OrderType.Market, over, 0, 0,
                key + "#fix" + fixedSoFar, MasterIdFromName(original.Name));
        }

        /// <summary>v2.4: una copia TPX que el bróker sigue mostrando viva sin nada por ejecutar ("0 Sell STP" en el gráfico,
        /// 16/9 14:23) no protege nada y confunde: se cancela una vez y se avisa. Las órdenes de la master no se tocan.</summary>
        private void SweepPhantoms()
        {
            if ((DateTime.Now - lastPhantomSweep).TotalSeconds < 5) return;
            lastPhantomSweep = DateTime.Now;
            try
            {
                List<Account> all;
                lock (Account.All) all = Account.All.ToList();
                foreach (Account a in all)
                {
                    List<Order> orders;
                    try { lock (a.Orders) orders = a.Orders.Where(IsLive).ToList(); } catch { continue; }
                    foreach (Order o in orders)
                    {
                        if (o.OrderType == OrderType.Market || o.Quantity - o.Filled > 0) continue;
                        string id = a.Name + "|" + OrderKey(o);
                        if (!phantomHandled.TryAdd(id, true)) continue;
                        string what = string.Format("{0} {1} {2} ({3}/{4} ejecutados, estado {5}) en {6}", o.OrderAction, o.Quantity, o.Instrument != null ? o.Instrument.FullName : "?",
                            o.Filled, o.Quantity, o.OrderState, a.Name);
                        if (a.Name == cfg.MasterAccount || !(o.Name ?? "").StartsWith("TPX "))
                        {
                            Warn("Orden fantasma (sin nada por ejecutar) que no es una copia nuestra: " + what + ". Cancélala a mano si molesta");
                            continue;
                        }
                        Warn("Copia fantasma (sin nada por ejecutar): " + what + ". Cancelando");
                        try { a.Cancel(new[] { o }); } catch (Exception cx) { Warn("No se pudo cancelar la copia fantasma " + id + ": " + cx.Message); }
                    }
                }
            }
            catch (Exception ex) { Error("SweepPhantoms: " + ex.Message); }
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
                // v2.4: ¿es la copia original de una reconciliación, ejecutando después de "cancelarse"?
                string rkey = account + "|" + MasterIdFromName(order.Name);
                Order orig;
                if (reconciledOriginals.TryGetValue(rkey, out orig) && ReferenceEquals(orig, order)) SettleExcess(rkey);
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

        /// <summary>v2.3: orden del master por su clave, para saber si un fill fue parcial (su orden sigue viva con resto).</summary>
        private Order FindMasterOrder(string masterOrderId)
        {
            Account m = master;
            if (m == null || string.IsNullOrEmpty(masterOrderId)) return null;
            try
            {
                List<Order> orders;
                lock (m.Orders) orders = m.Orders.ToList();
                return orders.FirstOrDefault(o => OrderKey(o) == masterOrderId);
            }
            catch { return null; }
        }

        /// <summary>Posición con signo del follower en ese instrumento (+largo / -corto / 0).</summary>
        private static int SignedPosition(Account account, string symbol)
        {
            try
            {
                lock (account.Positions)
                {
                    foreach (Position p in account.Positions)
                        if (p.Instrument != null && p.Instrument.FullName == symbol && p.Quantity != 0)
                            return p.MarketPosition == MarketPosition.Short ? -p.Quantity : p.Quantity;
                }
            }
            catch { }
            return 0;
        }

        /// <summary>Orden en cualquier estado no terminal (incluye Initialized/Submitted): sirve para no duplicar.</summary>
        private static bool IsLive(Order o)
        {
            return o.OrderState != OrderState.Filled && o.OrderState != OrderState.Cancelled
                && o.OrderState != OrderState.Rejected && o.OrderState != OrderState.Unknown;
        }

        private void OnFollowerPosition(object sender, PositionEventArgs e)
        {
            try
            {
                string account = e.Position != null && e.Position.Account != null ? e.Position.Account.Name : "";
                Publish(Json.Obj(
                    "msg_type", "POSITION",
                    "account", account,
                    "symbol", e.Position.Instrument.FullName,
                    "market_position", e.MarketPosition.ToString(),
                    "quantity", e.Quantity,
                    "avg_price", e.AveragePrice,
                    "timestamp", Now()));
            }
            catch (Exception ex) { Error("OnFollowerPosition: " + ex.Message); }
        }

        /// <summary>Cierre de emergencia: cancela todas las órdenes vivas de la cuenta y cierra sus posiciones.</summary>
        private string Flatten(string accountName)
        {
            // un cierre de emergencia manda: nada pendiente de reconciliar debe reabrir posición después
            foreach (string k in reconcileRequested.Keys) { int d; if (k.StartsWith(accountName + "|")) reconcileRequested.TryRemove(k, out d); }
            Account account;
            lock (Account.All) account = Account.All.FirstOrDefault(a => a.Name == accountName);
            if (account == null) return "ERROR|cuenta desconocida: " + accountName;
            int closed = 0;
            try
            {
                List<Instrument> instruments;
                lock (account.Positions) instruments = account.Positions.Where(p => p.Quantity != 0).Select(p => p.Instrument).Distinct().ToList();
                List<Order> live;
                lock (account.Orders) live = account.Orders.Where(IsLive).ToList();
                foreach (Order o in live)
                    if ((o.Name ?? "").StartsWith("TPX ")) flattenedKeys[accountName + "|" + MasterIdFromName(o.Name)] = true;
                if (live.Count > 0) { try { account.Cancel(live); } catch (Exception cx) { Warn("Flatten: cancelando órdenes: " + cx.Message); } }
                if (instruments.Count > 0)
                {
                    account.Flatten(instruments);   // NinjaTrader cancela lo que quede y cierra a mercado
                    closed = instruments.Count;
                }
                Info(string.Format("FLATTEN {0}: {1} órdenes canceladas, {2} instrumentos cerrados", accountName, live.Count, closed));
                Publish(Json.Obj("msg_type", "FLATTENED", "account", accountName, "orders_cancelled", live.Count,
                    "instruments_closed", closed, "timestamp", Now()));
                return "OK|" + accountName + "|" + closed;
            }
            catch (Exception ex)
            {
                Error("Flatten " + accountName + ": " + ex.Message);
                return "ERROR|" + ex.Message;
            }
        }

        private string PositionsReply()
        {
            var parts = new List<string>();
            List<Account> all;
            lock (Account.All) all = Account.All.ToList();
            foreach (Account a in all)
            {
                List<Position> positions;
                try { lock (a.Positions) positions = a.Positions.Where(p => p.Quantity != 0).ToList(); } catch { continue; }
                foreach (Position p in positions)
                    parts.Add(a.Name + "|" + p.Instrument.FullName + "|" + p.MarketPosition + "|" + p.Quantity + "|"
                        + p.AveragePrice.ToString("0.########", CultureInfo.InvariantCulture));
            }
            return string.Join(";", parts);
        }

        /// <summary>v2.2: órdenes vivas de todas las cuentas (stops, TPs, entradas pendientes), para la consola.
        /// acc|orderKey|masterId|action|symbol|qty|filled|type|limit|stop|state;...</summary>
        private string OrdersReply()
        {
            var parts = new List<string>();
            List<Account> all;
            lock (Account.All) all = Account.All.ToList();
            foreach (Account a in all)
            {
                List<Order> orders;
                try { lock (a.Orders) orders = a.Orders.Where(IsLive).ToList(); } catch { continue; }
                foreach (Order o in orders)
                {
                    if (o.Instrument == null) continue;
                    parts.Add(string.Join("|", new[] {
                        a.Name, OrderKey(o), MasterIdFromName(o.Name), ActionName(o.OrderAction), o.Instrument.FullName,
                        o.Quantity.ToString(CultureInfo.InvariantCulture), o.Filled.ToString(CultureInfo.InvariantCulture),
                        TypeName(o.OrderType), o.LimitPrice.ToString("0.########", CultureInfo.InvariantCulture),
                        o.StopPrice.ToString("0.########", CultureInfo.InvariantCulture), o.OrderState.ToString() }));
                }
            }
            return string.Join(";", parts);
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
                            double realized = 0, unrealized = 0;
                            try { realized = a.Get(AccountItem.RealizedProfitLoss, Currency.UsDollar); } catch { }
                            try { unrealized = a.Get(AccountItem.UnrealizedProfitLoss, Currency.UsDollar); } catch { }
                            parts.Add(a.Name.Replace("|", "/").Replace(";", ",") + "|"
                                + cash.ToString("0.00", CultureInfo.InvariantCulture) + "|"
                                + status + "|" + connection.Replace("|", "/").Replace(";", ",") + "|"
                                + realized.ToString("0.00", CultureInfo.InvariantCulture) + "|"
                                + unrealized.ToString("0.00", CultureInfo.InvariantCulture));
                        }
                    }
                    reply = string.Join(";", parts);
                }
                else if (request == "GET_MASTER")
                {
                    reply = cfg.MasterAccount;
                }
                else if (request == "GET_POSITIONS")
                {
                    reply = PositionsReply();
                }
                else if (request == "GET_ORDERS")
                {
                    reply = OrdersReply();
                }
                else if (request.StartsWith("FLATTEN|"))
                {
                    reply = Flatten(request.Substring("FLATTEN|".Length).Trim());
                }
                else if (request.StartsWith("WATCH|"))
                {
                    string name = request.Substring("WATCH|".Length).Trim();
                    Account acc;
                    lock (Account.All) acc = Account.All.FirstOrDefault(a => a.Name == name);
                    if (acc == null) reply = "ERROR|cuenta desconocida: " + name;
                    else { AttachFollower(acc); reply = "OK|" + name; }
                }
                else if (request.StartsWith("SET_MASTER|"))
                {
                    reply = SetMaster(request.Substring("SET_MASTER|".Length));
                }
                else if (request.StartsWith("ORDER|"))
                {
                    // v1.9: órdenes con confirmación. Si no respondemos, el engine lo sabe y reintenta; nunca se pierden en silencio.
                    reply = HandleFollowerMessage(request.Substring("ORDER|".Length));
                }
                else if (request == "PING")
                {
                    // v1.8: arranque y último seq publicado, para que el engine sepa si se está perdiendo eventos
                    reply = "PONG|" + bootId + "|" + System.Threading.Interlocked.Read(ref seq).ToString(CultureInfo.InvariantCulture);
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
            long n = System.Threading.Interlocked.Increment(ref seq);
            // cada mensaje lleva un número creciente: TradePilot detecta huecos y reinicios
            outbox.Enqueue("{\"seq\":" + n.ToString(CultureInfo.InvariantCulture) + "," + json.Substring(1));
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
                    File.WriteAllText(ConfigPath, ConfigJson(c), new UTF8Encoding(false));
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

        private void SaveConfig()
        {
            try
            {
                Directory.CreateDirectory(ConfigDir);
                File.WriteAllText(ConfigPath, ConfigJson(cfg), new UTF8Encoding(false));
            }
            catch (Exception ex) { Error("No se pudo guardar config.json: " + ex.Message); }
        }

        private static List<string> ParseList(string raw)
        {
            return (raw ?? "").Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(x => x.Trim().Trim('"')).Where(x => x.Length > 0).ToList();
        }

        private static string JsonList(List<string> items)
        {
            return "[" + string.Join(", ", items.Select(x => "\"" + x.Replace("\"", "") + "\"")) + "]";
        }

        private static string ConfigJson(BridgeConfig c)
        {
            return "{\n"
                + "  \"MasterAccount\": \"" + c.MasterAccount + "\",\n"
                + "  \"Host\": \"" + c.Host + "\",\n"
                + "  \"MasterPort\": " + c.MasterPort + ",\n"
                + "  \"FollowerPort\": " + c.FollowerPort + ",\n"
                + "  \"SyncPort\": " + c.SyncPort + ",\n"
                + "  \"PublishPrices\": " + (c.PublishPrices ? "true" : "false") + ",\n"
                + "  \"PriceThrottleMs\": " + c.PriceThrottleMs + ",\n"
                + "  \"HeartbeatMs\": " + c.HeartbeatMs + ",\n"
                + "  \"PriceInstruments\": " + JsonList(c.PriceInstruments) + ",\n"
                + "  \"AccountFilter\": " + JsonList(c.AccountFilter) + "\n"
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
