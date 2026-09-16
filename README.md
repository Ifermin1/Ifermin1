# TradePilot X

Replicador de órdenes para NinjaTrader con consola **web y móvil**. El motor corre junto a NinjaTrader; la consola se abre desde cualquier navegador o instalada como app en el teléfono.

Guía paso a paso (instalar, actualizar, operar, emergencias): [`docs/GUIA.md`](docs/GUIA.md). Diseño: [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md).

| Móvil | Escritorio |
|---|---|
| ![](docs/capturas/m2-dashboard.png) | ![](docs/capturas/d1-dashboard.png) |

## Arranque rápido (modo simulador, sin NinjaTrader)

```bash
# 1. Consola web
cd web && npm install && npm run build

# 2. Engine (sirve la API y la web en http://localhost:8000)
cd ../engine
pip install -e ".[dev]"
cp .env.example .env         # edita API_TOKEN
python -m tradepilot.main
```

Abre <http://localhost:8000>, pon la URL del engine y el token. En modo simulador, el botón **"Simular operación del maestro"** de la pestaña *Copiar* genera operaciones para ver el replicador trabajando.

## Actualizar a la última versión (Windows)

Con el engine parado (Ctrl+C en su ventana):

```powershell
cd $HOME\tradepilot
powershell -ExecutionPolicy Bypass -File scripts\update.ps1
```

Descarga los cambios, copia el addon actualizado a la carpeta de AddOns de NinjaTrader (con copia de seguridad del anterior) y arranca el engine. Si el addon cambió, compílalo en NinjaTrader (*New → NinjaScript Editor → F5*) y reinicia NinjaTrader.

## Probar desde el teléfono en local (misma Wi-Fi, sin dominio)

1. Arranca el engine en el PC. Al iniciar imprime algo como `Consola disponible en: http://localhost:8000 | http://192.168.1.40:8000`.
2. El puerto tiene que estar abierto en el firewall de Windows. `scripts/run_engine.ps1` crea la regla; si no, ejecútalo una vez como administrador o añádela a mano:
   ```powershell
   New-NetFirewallRule -DisplayName "TradePilot X" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000
   ```
3. En el teléfono abre `http://<IP-del-PC>:8000`, deja esa URL en el campo *URL del engine* y pon el `API_TOKEN`.
4. Para tenerla como app: en Android, menú de Chrome → *Añadir a pantalla de inicio*; en iOS, botón compartir de Safari → *Añadir a pantalla de inicio*.

Nota: sobre `http://` con IP (sin HTTPS) el navegador no registra el *service worker*, así que el icono en el escritorio funciona como acceso directo a pantalla completa pero no hay instalación "oficial" ni caché offline. La app funciona igual; la instalación completa llega cuando se ponga HTTPS (túnel), más adelante.

## Arranque en Windows con un solo comando

Abre PowerShell en tu carpeta de usuario (no en `C:\WINDOWS\system32`):

```powershell
cd $HOME
git clone -b claude/eager-cannon-n4krnv https://github.com/Ifermin1/Ifermin1 tradepilot
cd tradepilot
powershell -ExecutionPolicy Bypass -File scripts\run_engine.ps1
```

El script crea el entorno virtual de Python, compila la consola web si falta (necesita Node.js), crea `engine\.env`, abre el puerto en el firewall y arranca el engine. Requisitos: Python 3.11+, Node.js 18+ y Git.

## Con NinjaTrader real (PC Windows)

En modo `mock` el engine **no está conectado a NinjaTrader**: las operaciones son inventadas y lo que abras en NinjaTrader no llega. Para replicar operaciones reales:

1. En `engine\.env` cambia `ENGINE_MODE=mock` por `ENGINE_MODE=ninja` (y los puertos ZMQ si no son 5555/5556/5557).
2. Abre NinjaTrader con el addon ZMQ de TradePilot activo (el mismo que usaba la versión de escritorio).
3. Ctrl+C en el engine y arráncalo de nuevo con `scripts\run_engine.ps1`. En el Inicio de la consola el puente debe decir **NinjaTrader (ZMQ)** y la pestaña *Cuentas* debe mostrar tus cuentas reales al cabo de unos segundos.
4. En *Cuentas*, enciende el interruptor de cada cuenta que deba copiar a la maestra. Con el addon actualizado (`ninjatrader/TradePilotXBridge.cs`, ver `ninjatrader/README.md`) se listan **todas** las cuentas que NinjaTrader conoce, conectadas o no; con *Gestionar cuentas* decides cuáles están activas y les pones alias. Con el addon antiguo solo se ven las conectadas.

Si *Cuentas* queda vacío, el addon no está respondiendo en 5557: revisa que NinjaTrader esté abierto y el addon cargado. Sin NinjaTrader a mano puedes probar el modo `ninja` con `python scripts\fake_ninja.py`, que imita el addon.
3. Acceso desde fuera de casa (túnel Cloudflare / Tailscale): fase posterior, ver `docs/ARQUITECTURA.md` §2.

## Protecciones

- **Cerrar todo**: en *Riesgo*, "Cerrar todas las seguidoras" o "Cerrar TODO (incluida la maestra)". El kill switch puede cerrar además de bloquear. En *Cuentas*, cada cuenta con posición tiene su botón *Cerrar*.
- **Desincronización**: si una seguidora no tiene la posición de la maestra × multiplicador durante más de 6 s, aparece en rojo como DESINCRONIZADA, solo se le copian salidas, y el botón *Igualar a la maestra* manda la diferencia a mercado.
- **Stop rechazado**: si el bróker rechaza un stop copiado, el engine cierra esa cuenta al instante (`CLOSE_ON_STOP_REJECT=true` en `.env`).
- **Diario**: `engine\data\journal\AAAA-MM-DD.jsonl` guarda todo lo recibido y enviado. Ante cualquier incidente, ese archivo es lo que hay que revisar.

Todo esto requiere el addon **v2.1**; la consola avisa en rojo si el addon es anterior.

## Problemas comunes

- **`Proactor event loop does not implement add_reader`** en Windows: versión antigua del engine. Haz `git pull`; el arranque actual fuerza el bucle de eventos que ZMQ necesita.
- **`Timeout sincronizando cuentas (5557)`** o *Cuentas* vacío: NinjaTrader no está abierto o el addon no está cargado (en NinjaTrader, *Tools → Output* debe mostrar `[TradePilotX] Bridge online`).
- **`Failed to fetch`** en la pantalla de login: el engine no está arrancado. Comprueba que la ventana de PowerShell muestra `Uvicorn running`.

## Desarrollo

```bash
cd engine && python -m pytest          # pruebas del motor y la API
cd web && npm run dev                  # Vite con proxy a :8000 (hot reload)
```

Con Docker (solo modo simulador o con NinjaTrader accesible por red):

```bash
docker compose up --build
```
