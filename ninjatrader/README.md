# Addon de NinjaTrader 8: TradePilotXBridge

`TradePilotXBridge.cs` es el puente ZeroMQ entre NinjaTrader y el engine. Esta versión añade la petición
`GET_ACCOUNTS_ALL`, que reporta **todas** las cuentas que NinjaTrader conoce (conectadas o no) con su estado y
conexión, para que la consola las liste sin tocar `config.json`. `GET_ACCOUNTS` sigue igual, así que el engine
funciona también con el addon anterior (solo verá las conectadas y lo avisará en el log).

## Actualizar el addon compilado

1. Requisitos ya instalados de la versión anterior: `NetMQ.dll` y `AsyncIO.dll` en `Documents\NinjaTrader 8\bin\Custom`
   y añadidas como referencias en el editor NinjaScript (*Tools → NinjaScript Editor → click derecho → References*).
2. En NinjaTrader: *New → NinjaScript Editor*, carpeta *AddOns*, abre `TradePilotXBridge` (o crea uno con ese nombre).
3. Sustituye todo el contenido por el de `TradePilotXBridge.cs` y pulsa **F5** (compilar). Debe compilar sin errores.
4. Reinicia NinjaTrader (o desactiva y activa el addon). En *Tools → Output* debe aparecer
   `[TradePilotX] Bridge v2.2 online. master=... (v2.2: GET_ORDERS)`.

## Protocolo (puerto 5557, REQ/REP)

| Petición | Respuesta |
|---|---|
| `GET_ACCOUNTS` | `Sim101\|50000.00;Sim102\|25000.00` (maestra + conectadas, o `AccountFilter`) |
| `GET_ACCOUNTS_ALL` | `Sim101\|50000.00\|Connected\|MFF;APEX-1\|0.00\|Disconnected\|APEX TRADOVATE` (todas) |
| `GET_MASTER` | `Sim101` |
| `GET_POSITIONS` | `Sim101\|NQ SEP26\|Long\|2\|28936.0;...` posiciones abiertas de todas las cuentas |
| `FLATTEN\|Sim102` | `OK\|Sim102\|n` cancela todas las órdenes vivas de la cuenta y cierra sus posiciones a mercado |
| `WATCH\|Sim102` | `OK\|Sim102` suscribe órdenes/ejecuciones/posiciones de esa cuenta (ACK al engine) |
| `SET_MASTER\|Sim102` | `OK\|Sim102` o `ERROR\|motivo`. Cambia la cuenta maestra en caliente y la guarda en `config.json` |
| `ORDER\|{json}` | `OK\|tipo`, `IGNORED\|motivo` o `ERROR\|motivo` (v1.9). Mismo JSON que por 5556 |
| `GET_ORDERS` | `acc\|orderKey\|masterId\|action\|symbol\|qty\|filled\|type\|limit\|stop\|state;...` (v2.2) |
| `PING` | `PONG\|<boot>\|<seq>` (v1.8; antes `PONG`). `boot` cambia en cada arranque del addon y también viaja en el `HEARTBEAT` |

El campo de estado es el `ConnectionStatus` de NinjaTrader (`Connected`, `Disconnected`, `Connecting`...).
La cuenta maestra inicial se lee de `Documents\NinjaTrader 8\TradePilotX\config.json` (`MasterAccount`) y se puede cambiar desde la consola (v1.2+).

## Historial

- **2.2**: `GET_ORDERS` devuelve las órdenes vivas de todas las cuentas (stops, TPs, entradas pendientes) para que la
  consola las muestre junto a la posición.

- **2.1**: una copia cancelada por un `FLATTEN` (cierre de emergencia) queda marcada: si después llega el fill del master de
  esa misma orden, el addon no lo reconcilia a mercado (el 16/9 a las 12:14 esa reconciliación reabrió +2 tras cerrar la cuenta).

- **2.0**: el `EXECUTION` del master lleva `is_exit` / `is_entry` (lo dice el bróker) para que el engine nunca copie un
  cierre como una entrada. Si el master ejecuta una orden cuya copia sigue viva en el follower (stop movido a otro precio,
  cambio rechazado, cola), el addon cancela la copia y, al confirmarse la cancelación, cierra a mercado lo que quedó sin
  ejecutar (antes se ignoraba y el follower quedaba con un stop distinto al del master).

- **1.9**: las órdenes del engine llegan como `ORDER|{json}` por el canal de comandos (5557) y el addon las confirma
  (`OK|tipo`, `IGNORED|motivo`, `ERROR|motivo`). Antes iban por 5556 (PUB/SUB) sin confirmación y, tras un reinicio del
  addon, podían perderse en silencio (el engine las daba por enviadas). 5556 se mantiene para engines antiguos.

- **1.8**: `PING` responde `PONG|<boot>|<seq>` y el `HEARTBEAT` lleva `boot`. Con ello el engine detecta en unos segundos
  que el addon se reinició o que publica eventos que no le llegan (canal de eventos atascado tras recompilar o reconectar),
  reconecta el canal y vuelve a registrar las seguidoras (`WATCH`) sin esperar a los 15 s del vigilante del heartbeat.

- **1.7**: entradas con tolerancia: si la orden del engine trae `entry_mode=limit`, la copia se manda como límite al precio
  del master ± `tolerance_ticks` (ticks del instrumento de la seguidora); si no se llena en `entry_timeout_s`, `entry_fallback`
  "market" manda a mercado lo que falte y "cancel" la cancela (publica `ENTRY_MISSED`). Las salidas siguen a mercado.

- **1.6**: una entrada a mercado del master que se ejecuta en varios fills parciales se copia fill a fill (antes el
  segundo parcial se ignoraba y el follower quedaba corto de contratos). La protección contra salidas dobles se aplica
  solo a las copias de órdenes pendientes (stop / take profit).

- **1.5**: cierre de emergencia (`FLATTEN`), posiciones de todas las cuentas (`GET_POSITIONS` y eventos `POSITION` de
  followers), P&L realizado/flotante en `GET_ACCOUNTS_ALL`, número de secuencia `seq` en cada mensaje, y reconstrucción
  del estado (órdenes TPX vivas) al arrancar para no duplicar copias tras un reinicio.

- **1.4**: si la orden del follower (stop / take profit) ya se ejecutó, el fill del master para esa misma orden no se copia
  aunque llegue después (antes generaba una salida doble y dejaba al follower con posición contraria). Si la orden del
  follower quedó rechazada o cancelada, se copia a mercado solo lo que faltó por ejecutar. Cancelaciones también en
  estados de cambio pendiente.

- **1.3**: una orden pendiente del master se publica una sola vez (NinjaTrader emite `Accepted` y `Working` seguidos y se
  duplicaban las copias). El follower no acepta una segunda copia mientras la primera esté viva (`Initialized`/`Submitted`
  incluidos) y olvida las órdenes rechazadas para que un reintento pueda entrar.
- **1.2**: `SET_MASTER` / `GET_MASTER`.
- **1.1**: `GET_ACCOUNTS_ALL`, versión en el `HEARTBEAT`, sin doble suscripción a la master.
