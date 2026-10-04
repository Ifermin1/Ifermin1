# Plan: TradePilot Hub — guardar y analizar los trades de todos los usuarios, y replicar a un líder a distancia

Hoy cada usuario tiene **su engine en su PC** (junto a NinjaTrader) con **su base SQLite**. Nadie más ve sus
operaciones. Este plan describe cómo pasar a una **plataforma central (el Hub)** que recoja las ejecuciones y operaciones
de todos los engines, las analice y, cuando un usuario **líder lo decida**, permita que otros usuarios copien sus
operaciones **sin estar en su misma máquina**. Es un plan por fases: cada fase es útil por sí sola y no rompe lo que hay.

Principio que manda en todo el diseño: **el Hub es un espejo y un relé, nunca el que opera**. Las órdenes siempre las
manda el engine de cada usuario hacia su propio NinjaTrader con sus propios límites de riesgo. Si el Hub se cae, los
engines siguen funcionando en local como hoy.

---

## 1. Qué tenemos y qué falta

| Hoy (engine local) | Falta para el Hub |
|---|---|
| `trades` (operación cerrada: lado, contratos, precios medios, P&L, comisiones, fills, duración) | Identidad del usuario y del engine; copia al Hub |
| `executions` (cada orden copiada: precio del líder, fill de cada seguidora, deslizamiento, latencias) · solo en memoria (300) | Persistirlo y subirlo |
| `daily_stats`, `balance_daily`, `commissions_daily`, `cash_events` | Subirlo |
| `audit_logs` y el diario (`journal/*.jsonl`) de cada mensaje del addon | Subir solo lo relevante (eventos de órdenes y fills), no el ruido |
| Reglas de copia locales (seguidora → líder de la misma máquina) | Reglas **remotas** (seguidora de otro usuario → líder publicado en el Hub) |
| Token único por engine | Cuentas de usuario, dispositivos, permisos, consentimiento |
| Estadísticas en la consola (`lib/stats.ts`) | Las mismas, calculadas en el Hub sobre todos los usuarios, comparables |

---

## 2. Arquitectura objetivo

```
  Usuario A (PC)                         HUB (nube)                            Usuario B (PC)
 ┌──────────────┐    HTTPS (outbox)    ┌─────────────────────────┐   WebSocket   ┌──────────────┐
 │ NinjaTrader  │ ──────────────────▶  │ Ingesta  → Postgres/TSDB│ ◀──────────── │ NinjaTrader  │
 │ + engine A   │   trades, fills,     │ Analítica (vistas mat.) │  señales del  │ + engine B   │
 │ (líder)      │   executions, stats  │ Publicación/consentim.  │  líder A      │ (seguidor    │
 │              │ ◀──────────────────  │ Relé de señales         │ ────────────▶ │  remoto)     │
 └──────────────┘   acks, config       │ Web del Hub (ranking…)  │  acks/fills   └──────────────┘
                                       └─────────────────────────┘
```

Componentes nuevos:

1. **Reporter** (dentro del engine actual, `services/reporter_service.py`): cola de salida (*outbox*) en SQLite que
   sube al Hub todo evento nuevo con reintentos. Nunca bloquea la copia local.
2. **Hub API** (FastAPI, misma pila que el engine): ingesta, identidad, publicación, relé de señales y consultas.
3. **Base de datos del Hub**: PostgreSQL con TimescaleDB para series temporales (fills, muestras de P&L) y vistas
   materializadas para las estadísticas.
4. **Relé de señales**: cuando un líder está publicado, el Hub reenvía sus eventos de órdenes (no los fills de sus
   seguidoras) a los engines suscritos por WebSocket, y recibe de vuelta sus fills para medir la calidad de la copia
   remota.
5. **Web del Hub**: la consola actual ampliada con *Comunidad* (ranking, perfiles públicos, suscribirse a un líder) y
   un panel de administración.

---

## 3. Identidad y multiusuario

- **Usuario**: correo + contraseña (o Google/Apple), con 2FA opcional. `users(id, email, name, role, created_at)`.
- **Engine (dispositivo)**: cada instalación se registra una vez con un **código de emparejamiento** que el usuario
  genera en la web del Hub y pega en su `.env` (`HUB_URL`, `HUB_DEVICE_TOKEN`). `engines(id, user_id, name, version,
  last_seen, public_key)`. El token de dispositivo solo sirve para subir datos y recibir señales de ese usuario.
- **Cuenta de bróker**: `broker_accounts(id, engine_id, account_id, alias, firm, plan, plan_size, visible)`. La misma
  cuenta puede moverse de engine (reinstalación) conservando el histórico.
- Roles: `trader` (todos), `leader` (ha publicado al menos una cuenta), `admin` (tú).
- El token local del engine (`API_TOKEN`) sigue existiendo para la consola local; el Hub no lo conoce nunca.

---

## 4. Modelo de datos del Hub

Todas las tablas llevan `user_id` y `engine_id`; las filas se identifican con el id local más el engine para que la
subida sea **idempotente** (reintentar nunca duplica).

| Tabla | Clave | Contenido |
|---|---|---|
| `executions_raw` (hypertable) | `(engine_id, local_id)` | cada fill tal cual lo reporta NinjaTrader: cuenta, símbolo, lado, cantidad, precio, hora del bróker, id de orden, origen (líder / copia / manual / sync) |
| `copy_executions` | `(engine_id, master_order_id)` | una orden del líder copiada: precio y hora del líder; por seguidora: cantidad esperada/ejecutada, precio, deslizamiento en ticks, latencia engine / addon / bróker |
| `trades` | `(engine_id, local_id)` | operación cerrada (lo que hoy reconstruye `PerformanceService`) + `strategy_tag` opcional |
| `daily_stats`, `balance_daily`, `commissions_daily`, `cash_events` | `(engine_id, account, day)` | lo mismo que hoy |
| `risk_events` | `(engine_id, local_audit_id)` | solo los eventos de riesgo y copia que importan: BLOCKED, DESYNC, DAILY_LOSS_LIMIT, PROFIT_GOAL, DRAWDOWN_LIMIT, KILL_SWITCH, ACCOUNT_LOCKED |
| `rules_snapshots` | `(engine_id, ts)` | foto de las reglas de copia cada vez que cambian (para explicar un resultado: "copiaba ×2 desde el 3/10") |
| `engine_heartbeats` | `(engine_id, ts)` | versión, addon, latencias medias, conectado o no (para saber si un líder está "en línea") |

Lo que **no** se sube: tokens, el diario completo del addon, precios tick a tick (solo los que acompañan a un fill),
saldos si el usuario lo desactiva (opción "subir solo operaciones, no saldos").

Retención: fills crudos 2 años, trades y agregados para siempre, heartbeats 90 días.

---

## 5. Ingesta: cómo salen los datos del engine

1. Cada vez que el engine escribe un trade, fill, execution, stat o evento de riesgo, también escribe una fila en
   `outbox(id, kind, payload_json, created_at, sent_at NULL)` dentro de la **misma transacción** SQLite.
2. Un bucle cada 2 s manda lo pendiente en lotes de hasta 500 filas a `POST /v1/ingest` con el token de dispositivo.
   El Hub responde con los ids aceptados; el engine marca `sent_at`. Si no hay red, se acumula y se reintenta con
   espera creciente (2 s → 60 s). El lote lleva `engine_version` y `schema_version`.
3. Al emparejar un engine por primera vez se hace una **carga inicial**: todo el histórico local (`trades`,
   `daily_stats`…) se encola en el outbox. Así nadie pierde lo que ya tenía.
4. Las ejecuciones (`executions`) pasan de estar solo en memoria a guardarse en SQLite (`copy_executions` local) para
   poder subirlas y para que la calidad de ejecución sobreviva a reinicios.
5. Reloj: el engine manda su hora y el Hub guarda también la de recepción; el desfase se muestra en el perfil del
   engine (si pasa de 2 s se avisa, porque afecta a las latencias medidas).

---

## 6. Analítica central

Se calculan en el Hub, con **las mismas definiciones que `web/src/lib/stats.ts`** (una sola fuente de verdad: el
módulo se porta a Python en `hub/analytics.py` y se prueba contra los mismos casos):

- Por cuenta y por usuario: resultado bruto/neto, aciertos, factor de beneficio, esperanza (y en R), media
  ganadora/perdedora, mayor ganancia/pérdida, rachas, drawdown máximo de la curva, Sharpe/Sortino/Calmar, duración
  media, por hora / día de la semana / símbolo / dirección.
- **Calidad de copia** por líder y por seguidor: % de entradas al mismo precio, deslizamiento medio en ticks, latencia
  mediana, entradas perdidas, desincronizaciones.
- **Consistencia** (lo que miran los prop firms): % del mejor día sobre el total, días operados, tamaño medio.
- Vistas materializadas refrescadas cada 5 minutos (`mv_account_stats_30d`, `mv_account_stats_all`,
  `mv_leader_quality`), más consultas directas para los rangos personalizados.
- Para el ranking: solo cuentas **verificadas** (ver §7) y con un mínimo (p. ej. 30 operaciones y 15 días).

Lo que ve cada usuario: lo suyo, completo (lo mismo que hoy en Análisis, pero con todos sus engines juntos). Lo que ve
la comunidad: solo los perfiles publicados y con las cifras que el líder haya decidido mostrar.

---

## 7. Publicar: consentimiento y verificación

Un usuario puede **publicar una cuenta** (no un usuario entero): `publications(id, user_id, broker_account_id, slug,
title, description, visibility, show_balance, show_symbols, delay_seconds, status, created_at)`.

- `visibility`: `private` (solo yo), `stats` (ranking y estadísticas, sin señales), `signals` (además se puede copiar).
- Consentimiento explícito en pantalla: qué se muestra, qué se comparte, que puede retirarlo cuando quiera. Al
  despublicar, las señales paran al instante y las estadísticas desaparecen del ranking (el histórico privado sigue).
- **Verificación**: una cuenta es "verificada" cuando sus operaciones llegan desde un engine emparejado y en línea (no
  importadas a mano). Las importaciones manuales se marcan como *no verificadas* y no entran en el ranking.
- Opcional: límite de seguidores, lista de permitidos (solo mis amigos), y `delay_seconds` para publicar estadísticas
  con retraso sin dar señales en tiempo real.

---

## 8. Copia remota (señales) — el "en un futuro"

Es la pieza que hace que un líder en Miami y un seguidor en Madrid copien sin compartir PC.

1. **Suscripción**: el seguidor, en la web del Hub, elige un líder publicado con `signals` y crea una *regla remota*:
   qué cuenta suya copia, multiplicador, símbolo destino, modo de entrada (igual que las opciones de hoy). La regla se
   guarda en el Hub y se envía a su engine, que la trata como una regla más (`source = hub:<publication_id>`).
2. **Canal**: el engine del líder ya manda cada evento de orden al Hub (es parte del outbox, pero por un canal
   prioritario: WebSocket persistente, no el lote de 2 s). El Hub lo reenvía en milisegundos a los engines suscritos.
3. **El engine del seguidor** recibe `ORDER_PENDING / EXECUTION / ORDER_MODIFIED / ORDER_CANCELLED` del líder como si
   vinieran de su propio feed del líder y aplica **exactamente la misma lógica de hoy**: límites de riesgo, kill switch,
   corte del día, objetivo, drawdown, desincronización. Nada nuevo que pueda equivocarse.
4. **Protecciones específicas del remoto**:
   - *Latencia máxima*: si una señal llega con más de N ms (configurable, p. ej. 1500) se trata como `ENTRY_MISSED`
     y no se entra a mercado tarde; las salidas sí se aplican siempre.
   - *Huecos*: secuencia por publicación; si falta un mensaje, el seguidor pide el estado actual del líder
     (`GET /v1/publications/{id}/state`: posiciones y órdenes vivas) y se resincroniza, como hoy con `RESYNC`.
   - *Líder desconectado*: si el líder deja de latir más de 30 s, el Hub avisa a los seguidores; opción por regla de
     "cerrar mis posiciones de este líder si pierde la conexión más de X minutos".
   - *Tamaño*: el seguidor copia en proporción a su regla; el Hub nunca ve ni toca el tamaño de nadie.
5. **Calidad medida**: los fills del seguidor vuelven al Hub con el `master_order_id` y se calcula el deslizamiento
   remoto, que aparece en el perfil del líder ("sus seguidores entran de media 1,8 ticks peor, 420 ms después").
6. **Qué no se copia nunca** por el canal remoto: operaciones manuales de las seguidoras del líder, cierres de
   emergencia del líder provocados por sus límites (se mandan como "el líder cerró por riesgo" y el seguidor decide
   según su propia regla), ni nada de cuentas no publicadas.

---

## 9. Seguridad y privacidad

- Todo por HTTPS; tokens de dispositivo de un solo uso al emparejar y rotables; cada engine firma sus lotes (clave
  por dispositivo) para que nadie pueda inyectar operaciones falsas en un perfil.
- Las señales van cifradas de extremo a extremo entre líder y seguidores suscritos (el Hub reenvía sin poder leer el
  contenido si se activa el modo "privado"); por defecto el Hub las lee para medir calidad.
- Datos personales mínimos (correo). Los nombres de cuenta del bróker se muestran por defecto como alias.
- Derecho a borrar: `DELETE /v1/me` elimina usuario, engines y datos; las estadísticas agregadas anónimas se quedan.
- Registro de auditoría del Hub (quién publicó, quién se suscribió, cuándo se despublicó).
- Límites por usuario (ingesta, señales) para que un engine roto no tumbe el Hub.

---

## 10. Fases y entregables

| Fase | Qué se entrega | Resultado visible |
|---|---|---|
| **0. Exportar** (1 semana) | `GET /api/export` en el engine (ZIP con trades, fills, stats en JSON/CSV); `copy_executions` persistido en SQLite | Cada usuario puede sacar todo su histórico; la calidad de ejecución sobrevive a reinicios |
| **1. Hub de ingesta** (3 semanas) | Hub API + Postgres; registro de usuario y emparejamiento de engine; `ReporterService` con outbox; carga inicial | En la web del Hub cada usuario ve sus cuentas y operaciones de todos sus engines |
| **2. Analítica** (2 semanas) | `hub/analytics.py` (port de `stats.ts`, misma suite de pruebas), vistas materializadas, página *Mis estadísticas* en el Hub | Lo mismo que Análisis hoy, pero sobre todos los engines y con histórico ilimitado |
| **3. Publicar** (2 semanas) | Publicaciones, consentimiento, verificación, *Comunidad* (ranking, perfil público con estadísticas) | Un líder puede mostrar su rendimiento; los demás pueden verlo |
| **4. Señales remotas** (4 semanas) | Canal WebSocket, reglas remotas, protecciones de latencia/huecos/desconexión, calidad remota | Un seguidor copia a un líder de otra máquina |
| **5. Operación** (continuo) | Panel de administración, alertas del Hub, copias de seguridad, costes, facturación opcional (suscripción al líder) | Plataforma mantenible |

Las fases 0 a 2 no cambian nada de la copia local; la fase 4 es la única que toca la replicación y reutiliza el
`ReplicationService` tal cual, con un "bridge" nuevo (`HubSignalBridge`) que implementa la misma interfaz que el
puente ZMQ del líder.

---

## 11. Pila y costes orientativos

- Hub: FastAPI + PostgreSQL 16 con TimescaleDB, Redis para el relé de señales, un contenedor por servicio (Docker
  Compose al principio; Kubernetes solo si hace falta). Un VPS de 4 vCPU / 8 GB aguanta cientos de engines
  (~30-60 €/mes). Dominio + Cloudflare delante (ya lo usamos para el túnel).
- Engine: sin dependencias nuevas salvo el `ReporterService`.
- Web: la consola actual con una sección nueva; el Hub sirve la misma aplicación con un modo "comunidad".

---

## 12. Decisiones que necesitan tu revisión

1. **Qué cuenta se publica**: por cuenta (propuesto) o por usuario entero.
2. **Saldos**: subir saldos y retiros por defecto (necesario para el drawdown real y la consistencia) o solo operaciones.
3. **Latencia máxima** de una señal remota antes de no entrar (propuesto 1,5 s) y qué hacer si el líder se desconecta.
4. **Ranking**: mínimo de operaciones y días para aparecer, y si se ordena por neto, por factor de beneficio o por una
   puntuación como la del Calendario.
5. **Monetización**: gratis entre amigos, suscripción al líder con comisión del Hub, o cuota fija. Afecta a la fase 5 y
   a qué datos hay que guardar desde la fase 1.
6. **Dónde vive el Hub**: tu VPS (control total) o un proveedor gestionado (menos trabajo, más coste).
