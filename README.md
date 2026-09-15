# TradePilot X

Replicador de órdenes para NinjaTrader con consola **web y móvil**. El motor corre junto a NinjaTrader; la consola se abre desde cualquier navegador o instalada como app en el teléfono.

Lee el diseño completo en [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md).

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

## Probar desde el teléfono en local (misma Wi-Fi, sin dominio)

1. Arranca el engine en el PC. Al iniciar imprime algo como `Consola disponible en: http://localhost:8000 | http://192.168.1.40:8000`.
2. El puerto tiene que estar abierto en el firewall de Windows. `scripts/run_engine.ps1` crea la regla; si no, ejecútalo una vez como administrador o añádela a mano:
   ```powershell
   New-NetFirewallRule -DisplayName "TradePilot X" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000
   ```
3. En el teléfono abre `http://<IP-del-PC>:8000`, deja esa URL en el campo *URL del engine* y pon el `API_TOKEN`.
4. Para tenerla como app: en Android, menú de Chrome → *Añadir a pantalla de inicio*; en iOS, botón compartir de Safari → *Añadir a pantalla de inicio*.

Nota: sobre `http://` con IP (sin HTTPS) el navegador no registra el *service worker*, así que el icono en el escritorio funciona como acceso directo a pantalla completa pero no hay instalación "oficial" ni caché offline. La app funciona igual; la instalación completa llega cuando se ponga HTTPS (túnel), más adelante.

## Con NinjaTrader real (PC Windows)

1. En `engine/.env`: `ENGINE_MODE=ninja` (y los puertos ZMQ si no son 5555/5556/5557).
2. `powershell -File scripts/run_engine.ps1`
3. Acceso desde fuera de casa (túnel Cloudflare / Tailscale): fase posterior, ver `docs/ARQUITECTURA.md` §2.

## Desarrollo

```bash
cd engine && python -m pytest          # pruebas del motor y la API
cd web && npm run dev                  # Vite con proxy a :8000 (hot reload)
```

Con Docker (solo modo simulador o con NinjaTrader accesible por red):

```bash
docker compose up --build
```
