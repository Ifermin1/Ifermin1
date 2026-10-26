# TradePilot X · Ficha de ubicación, instalación y acceso

Preparada el **24/10/2026 a las 14:52 UTC** desde un entorno en la nube que **solo tiene el código** (un clon del
repositorio). Ese entorno **no tiene acceso al PC de trading** ni a ningún servidor del proyecto: todo lo que dice "no
verificado" hay que comprobarlo en el PC con los comandos de lectura del apartado 7. Esta ficha no contiene tokens,
claves ni contenido de `.env`, y para elaborarla no se envió ninguna orden ni se cambió ninguna configuración.

## Tabla resumen

| Dato | Valor | Fuente de verificación | Pendiente |
|---|---|---|---|
| Repositorio | https://github.com/Ifermin1/Ifermin1 (público) | `git remote -v` y API de GitHub (`private: false`) | No |
| Rama de trabajo | `claude/eager-cannon-n4krnv` (también rama por defecto del repositorio) | `git branch --show-current`; API de GitHub `default_branch` | No |
| Último commit revisado | `65c65ccce54b1f25db3f76301636a518f438f143` · 24/10/2026 14:27 UTC · "Docs: hoja de ruta para comercializar y prompt maestro para agentes" | `git log -1`; API de GitHub `commits/claude/eager-cannon-n4krnv` | No |
| Copia local = GitHub | Sí: `HEAD` y `origin/claude/eager-cannon-n4krnv` apuntan al mismo SHA, árbol limpio | `git fetch` + `git rev-parse HEAD origin/<rama>` + `git status --short` (0 líneas) | No |
| Pull requests | Ninguno (ni abierto ni cerrado) | API de GitHub `pulls?state=all` | No |
| CI | Verde en los últimos commits de código (`ed18c27`, `17f56a8`, `a5437af`, `042511a`); el commit `65c65cc` solo cambia documentación | API de GitHub `actions/runs?head_sha=` | No |
| Documentación principal | `README.md`, `docs/GUIA.md` (manual de usuario), `docs/ARQUITECTURA.md`, `docs/COMERCIALIZACION.md`, `hub/README.md`, `engine/.env.example` (plantilla sin secretos) | Listado del repositorio | No |
| Versiones del código | engine `0.2.0` (`engine/pyproject.toml`, Python ≥ 3.11) · consola `0.2.0` (`web/package.json`) · addon `2.9` (`ninjatrader/TradePilotXBridge.cs`, `BridgeVersion`) · addon mínimo aceptado `2.6`, mínimo para operar desde la consola `2.7` (`engine/tradepilot/core/config.py`) | Archivos citados | No |
| Ruta del proyecto en el PC | `C:\Users\ibrah\Documents\TradePilotX` | **Solo por lo indicado por el dueño en la conversación**; el clon no puede verla | **Sí** (paso 7.1) |
| Componentes en el PC | Addon dentro de NinjaTrader 8 · engine Python (puerto 8000 por defecto) · consola web servida por el engine · base de datos SQLite `engine\data\tradepilot.db` · journal `engine\data\journal` · tarea programada "TradePilotX Engine" (si se ejecutó `scripts\install_autostart.ps1`) · túnel Cloudflare, tarea "TradePilotX Tunnel" (si se ejecutó `scripts\publish.ps1`) | Documentación y scripts del repositorio; **no comprobado en el PC** | **Sí** (7.2, 7.3) |
| Componentes en servidores externos | **Ninguno.** En Internet solo está el código (GitHub). El hub (`hub/`) existe en el código y no está desplegado | Repositorio; ausencia de configuración de despliegue | No |
| Estado actual de engine / NinjaTrader / addon / túnel | **No verificable desde este entorno.** Última evidencia: logs y auditoría pegados por el dueño el 21/10 y 23/10/2026 mostraban el engine en modo `ninja` con NinjaTrader conectado y cuentas sincronizadas | Mensajes del dueño en la conversación | **Sí** (7.2, 7.4) |
| URL local | `http://localhost:8000` **solo en el propio PC** (puerto `API_PORT`, por defecto 8000) | `config.py` y `scripts/update.ps1` | Confirmar puerto (7.1) |
| Dirección de red local | `http://<IP-del-PC>:8000` desde la misma Wi-Fi; el engine la imprime al arrancar ("Consola disponible en…") | `engine/tradepilot/container.py` | **Sí** (7.4) |
| URL remota vigente | **Desconocida desde aquí.** Si existe, está en `engine\data\public_url.txt` (la escribe `scripts\publish.ps1`) o en `PUBLIC_URL=` de `engine\.env`; la consola la muestra en el menú lateral → **Acceso remoto**. Un túnel rápido sin dominio cambia de URL en cada arranque. Las direcciones `*.midominio.com` de la documentación son ejemplos | `scripts/publish.ps1`, `config.py` (`PUBLIC_URL_FILE`), endpoint `/api/health` (`public_url`) | **Sí** (7.3) |
| Autenticación | Un único token Bearer (`API_TOKEN` en `engine\.env`); la consola lo pide en la pantalla de entrada y lo guarda en el navegador; el WebSocket lo acepta por cabecera o `?token=`. Bloqueo de IP 5 min tras 10 tokens distintos inválidos en 10 min | `engine/tradepilot/api/auth.py` | No |
| Usuario o rol de solo lectura | **Existe desde el 26/10/2026**: `API_READ_TOKEN` en `engine\.env` (opcional). Con él se ve todo y el engine rechaza (403) cualquier orden, cierre o cambio. Si no está definido, el único token lo puede todo | `engine/tradepilot/api/auth.py` | Confirmar si está definido en el PC |
| Pruebas con simulador | Suite del engine: 124 tests (`engine/tests`, modo `mock`) · hub: 2 tests · build de la consola · pruebas de extremo a extremo con Playwright contra el simulador (coach, semáforo, vista de bolsillo, informe, CSV, hub) · CI "Escenarios" con `scripts/fake_ninja.py` (NinjaTrader falso por ZeroMQ, `ENGINE_MODE=ninja`) | Ejecutadas en el entorno de la nube el 24/10/2026; CI de GitHub | No |
| Pruebas dentro de NinjaTrader real | **No realizadas por el asistente.** La única evidencia de funcionamiento real son los logs y la auditoría que el dueño pegó (21/10 y 23/10): copia activa, stops ejecutados, un episodio de token inválido (429/403) corregido en los commits `857733a` y `9ab3b69` | Conversación | **Sí** (7.4) |
| Última comprobación de salud | Ninguna sobre la instalación real desde este entorno. `GET /api/health` (con token) devuelve modo, conexión del addon, versión del addon, fecha de compilación de la consola y `public_url` | `engine/tradepilot/api/routes.py` | **Sí** (7.4) |
| Errores de conexión conocidos | 21–23/10: WebSocket 403 y HTTP 429 por un token antiguo guardado en el navegador; resuelto (la consola avisa "Salir y volver a entrar"). Sin otros errores reportados | Conversación y commits | No |

## 1. Código

- URL: https://github.com/Ifermin1/Ifermin1
- Rama: `claude/eager-cannon-n4krnv`. Es la rama por defecto del repositorio, así que la página principal de GitHub la
  muestra directamente.
- SHA revisado: `65c65ccce54b1f25db3f76301636a518f438f143`
  (https://github.com/Ifermin1/Ifermin1/commit/65c65ccce54b1f25db3f76301636a518f438f143).
- Verificación: el 24/10/2026 a las 14:52 UTC se ejecutó `git fetch origin` y `git rev-parse HEAD origin/claude/eager-cannon-n4krnv`
  (mismo SHA) y `git status --short` (sin cambios). La API pública de GitHub devolvió el mismo SHA como cabeza de la rama.
- Documentación: `README.md` · `docs/GUIA.md` · `docs/ARQUITECTURA.md` · `docs/COMERCIALIZACION.md` · `hub/README.md` ·
  `engine/.env.example`. Scripts de operación en `scripts/` (`update.ps1`, `run_engine.ps1`, `install_autostart.ps1`,
  `publish.ps1`, `fake_ninja.py`, `ci_scenarios.py`).

## 2. Instalación

**Comprobado en el código:** el proyecto se instala y actualiza con `scripts\update.ps1` (crea `engine\.venv`, instala el
engine con `pip install -e .`, compila la consola con `npm run build`); el engine escucha en `0.0.0.0:8000` por defecto y
sirve la consola desde `web\dist`; los datos van a `engine\data`; el addon se compila dentro de NinjaTrader (F5).

**Solo según la documentación o la conversación (no verificado en el PC):** ruta `C:\Users\ibrah\Documents\TradePilotX`;
existencia de las tareas programadas "TradePilotX Engine" y "TradePilotX Tunnel"; versión del addon realmente cargada en
NinjaTrader; versión de NinjaTrader.

**Servidores externos:** ninguno. No hay despliegue de la consola, del engine ni del hub fuera del PC. Si hay acceso remoto,
es un túnel de Cloudflare que termina en el PC: con el PC o el túnel apagados no hay nada accesible desde fuera.

## 3. Estado actual

El asistente que elaboró esta ficha **no tiene acceso al PC** y no puede afirmar que el engine, NinjaTrader, el addon o el
túnel estén activos. La última evidencia de funcionamiento real son logs pegados por el dueño los días 21 y 23 de octubre
de 2026. Cualquier revisión del estado debe hacerse con los comandos del apartado 7.

## 4. Direcciones

- Local, solo en el PC: `http://localhost:8000` (si `API_PORT` no se cambió).
- Red local: la IP del PC con el puerto 8000; el engine la imprime al arrancar. No se conoce desde aquí.
- Remota: no se conoce desde aquí. Dónde mirar: `engine\data\public_url.txt`, `PUBLIC_URL=` en `engine\.env` (sin leer el
  resto del archivo) o el menú lateral de la consola → **Acceso remoto**. No se ha activado ni publicado ningún acceso
  nuevo para esta ficha.

## 5. Acceso

1. Abrir la URL local o remota en el navegador.
2. Introducir el token de `engine\.env` (`API_TOKEN=`) en la pantalla de entrada. No hay usuarios ni contraseñas.
3. Si `API_READ_TOKEN` está definido en `engine\.env`, entrar con ese token: la consola queda en modo solo lectura y el engine rechaza cualquier acción. Si no lo está, el único token lo puede todo; para revisar sin operar: quedarse en Inicio, Cuentas, Análisis, Calendario y
   Auditoría; no pulsar COMPRAR/VENDER en Operar, ni "Guardar", "Cerrar" o "Cerrar todo" en Riesgo o Cuentas, ni el botón
   rojo DETENER de la barra superior. Todas esas acciones piden confirmación y quedan registradas en Auditoría.
4. Alternativa sin interfaz: `GET /api/health`, `GET /api/accounts`, `GET /api/risk` y `GET /api/audit?limit=50` con el
   token en la cabecera `Authorization: Bearer …` son de solo lectura.

## 6. Diagnóstico

- Versiones del código: engine 0.2.0, consola 0.2.0, addon 2.9 (mínimo aceptado 2.6; mínimo para operar desde la consola 2.7).
- Pruebas con simulador (`ENGINE_MODE=mock`) y con NinjaTrader falso (`scripts/fake_ninja.py`, modo `ninja` por ZeroMQ):
  124 tests del engine, 2 del hub, build de la consola y escenarios de CI, todo en verde el 24/10/2026.
- Pruebas dentro de NinjaTrader real: ninguna hecha por el asistente. Evidencia aportada por el dueño: auditoría del
  21/10 y 23/10 con copia activa y stops ejecutados; incidente de token inválido (WebSocket 403, HTTP 429) resuelto.
- Última comprobación de salud sobre la instalación real: no realizada desde este entorno.

## 7. Pendientes: qué debe ejecutar el dueño (solo lectura, sin secretos)

En PowerShell, desde `C:\Users\ibrah\Documents\TradePilotX` (ajustar si la ruta es otra). Pegar la salida tal cual; ninguno
de estos comandos muestra el token.

1. **Ruta, rama y commit instalados**
   ```powershell
   Get-Location; git branch --show-current; git log -1 --format="%H %cd"
   ```
2. **Procesos y tareas** (engine, NinjaTrader, túnel)
   ```powershell
   Get-Process python, NinjaTrader, cloudflared -ErrorAction SilentlyContinue | Select-Object Name, Id, StartTime
   Get-ScheduledTask -TaskName "TradePilotX*" -ErrorAction SilentlyContinue | Select-Object TaskName, State
   ```
3. **URL remota vigente** (si el archivo no existe, no hay túnel configurado)
   ```powershell
   Get-Content engine\data\public_url.txt -ErrorAction SilentlyContinue
   Select-String -Path engine\.env -Pattern "^(PUBLIC_URL|API_PORT|API_HOST|ENGINE_MODE)=" | ForEach-Object { $_.Line }
   ```
4. **Salud del engine y conexión con el addon** (sustituir `TOKEN` por el token sin pegarlo en la respuesta: la salida no lo incluye)
   ```powershell
   $h = @{ Authorization = "Bearer TOKEN" }
   (Invoke-RestMethod http://localhost:8000/api/health -Headers $h) | Select-Object mode, web_build, public_url -ExpandProperty bridge | Format-List
   (Invoke-RestMethod http://localhost:8000/api/accounts -Headers $h) | Select-Object account_id, connected, enabled, updated_at | Format-Table
   ```
   Si no se quiere usar el token en la terminal: abrir la consola y leer en **Inicio** el estado del addon y en el menú
   lateral **Acceso remoto**.
5. **Versión del addon cargada en NinjaTrader**: en la consola, Inicio muestra la versión del addon; o en NinjaTrader,
   Control Center → Tools → Options → NinjaScript, comprobar que `TradePilotXBridge` está compilado.

## Resumen: enlaces utilizables

- Repositorio: https://github.com/Ifermin1/Ifermin1
- Rama: https://github.com/Ifermin1/Ifermin1/tree/claude/eager-cannon-n4krnv
- Commit revisado: https://github.com/Ifermin1/Ifermin1/commit/65c65ccce54b1f25db3f76301636a518f438f143
- Manual: https://github.com/Ifermin1/Ifermin1/blob/claude/eager-cannon-n4krnv/docs/GUIA.md
- Arquitectura: https://github.com/Ifermin1/Ifermin1/blob/claude/eager-cannon-n4krnv/docs/ARQUITECTURA.md
- CI: https://github.com/Ifermin1/Ifermin1/actions
- Consola: solo en el PC, `http://localhost:8000`; la dirección remota, si existe, está en `engine\data\public_url.txt`
  o en el menú **Acceso remoto** de la consola. No hay ningún otro enlace público del proyecto.
