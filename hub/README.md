# TradePilot X · Hub (fase 1)

Servidor pequeño y opcional que **reúne varios engines** (varios PCs, varias firmas) en un solo sitio: P&L del día de
cada uno, cuentas, posiciones, kill switch, operaciones de la semana y los últimos avisos. Los engines **empujan** los datos
(no hace falta abrir puertos en los PCs de trading): cada `HUB_INTERVAL_S` segundos mandan lo nuevo a `POST /api/ingest`
y, si el hub está caído, lo guardan en una cola local (`hub_outbox`) y lo reintentan.

## Arrancar el hub

En cualquier máquina con Python 3.11+ (un VPS, el propio PC, una Raspberry):

```powershell
pip install -r hub/requirements.txt
$env:HUB_TOKEN = "un-token-largo-y-aleatorio"      # Linux/macOS: export HUB_TOKEN=...
python -m uvicorn hub.app:app --host 0.0.0.0 --port 8100
```

Abre `http://<host>:8100/` y pega el token. La base de datos es `hub/hub.db` (cámbiala con `HUB_DB=`). Para exponerlo
fuera de casa, ponlo detrás de HTTPS (Cloudflare Tunnel, Caddy, Tailscale) igual que la consola.

## Conectar un engine

En `engine\.env` de cada PC:

```
HUB_URL=https://hub.midominio.com      # o http://192.168.1.50:8100 en casa
HUB_TOKEN=el-mismo-token
HUB_NODE=PC-Apex                       # nombre con el que aparece en el hub (vacío = nombre del PC)
```

y reinicia el engine (`scripts\update.ps1`). En la consola, *Riesgo → Hub* muestra la cola, el último envío y los errores,
y *Enviar ahora* fuerza un ciclo.

## API

| Método | Ruta | Qué |
|---|---|---|
| POST | `/api/ingest` | `{node:{id,mode,version}, items:[{kind: trade|snapshot|event, ref, at, data}]}`; idempotente (operaciones por `node+id`, avisos por `node+ref`) |
| GET | `/api/summary` | Nodos (último contacto, P&L del día, kill switch, cuentas, operaciones de hoy y de la semana), totales y últimos avisos |
| GET | `/api/trades?days=7&node=` | Operaciones recibidas |
| GET | `/api/events?limit=50` | Avisos recibidos |

Todas con `Authorization: Bearer <HUB_TOKEN>` (o `?token=`).

## Qué no hace todavía (fase 2)

Enviar órdenes a un engine desde el hub, usuarios y permisos, y gráficos. Lo de ahora es solo lectura: mirar todo de un
vistazo y tener el histórico de todas las cuentas en un sitio.
