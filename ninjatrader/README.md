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
   `[TradePilotX] Bridge v1.5 online. master=... (GET_ACCOUNTS_ALL, SET_MASTER disponibles)`.

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
| `PING` | `PONG` |

El campo de estado es el `ConnectionStatus` de NinjaTrader (`Connected`, `Disconnected`, `Connecting`...).
La cuenta maestra inicial se lee de `Documents\NinjaTrader 8\TradePilotX\config.json` (`MasterAccount`) y se puede cambiar desde la consola (v1.2+).

## Historial

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
