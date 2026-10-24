# TradePilot X · Qué falta para venderlo y prompt maestro para tu agente

Estado a 24/10/2026: la app funciona de extremo a extremo (copia entre cuentas de NinjaTrader, operar desde la web,
riesgo automático, trailing, calendario, coach IA, avisos push/Telegram, informe semanal, hub). Lo que hay es un producto
**para ti**. Para **venderlo** falta lo que convierte un proyecto personal en un producto: instalación en un clic, licencias,
usuarios, legal, soporte y un canal de distribución. Esta es la lista por orden de importancia.

## 1. Lo imprescindible antes de cobrar a nadie

| # | Qué | Por qué | Cómo (resumen) |
|---|---|---|---|
| 1 | **Instalador de Windows firmado** | Hoy hace falta Python, Node, `update.ps1` y compilar el addon con F5. Un cliente no hace eso. | Empaquetar engine + consola con PyInstaller (o Python embebido), instalador Inno Setup/MSI que copia el addon compilado a `Documents\NinjaTrader 8\bin\Custom`, crea el servicio de Windows (arranque automático) y el acceso directo. Certificado de firma de código (SmartScreen). |
| 2 | **Licencias y cobro** | Sin licencia no hay modelo de negocio ni forma de cortar el servicio a quien no paga. | Clave de licencia ligada a la máquina (hash de hardware), validación online con gracia de 7 días sin conexión, planes en Stripe (mensual/anual), prueba gratuita de 14 días. El engine muestra en la consola el estado de la licencia y bloquea solo la copia (nunca el cierre de posiciones). |
| 3 | **Usuarios, contraseñas y 2FA** | Hoy hay un solo token para todo. Un producto público necesita identidad, caducidad de sesión y recuperación. | Usuario + contraseña (argon2), 2FA TOTP opcional, sesiones con caducidad, roles (dueño / solo lectura), cierre de sesión remoto. |
| 4 | **Legal** | Mueve dinero real y usa IA: hay que cubrirse. | Términos de servicio, política de privacidad (RGPD si vendes en Europa), aviso de riesgo ("no es asesoramiento financiero", el coach IA es orientativo), limitación de responsabilidad, política de reembolsos. Revisar con un abogado las **reglas de cada prop firm sobre copy trading** (varias prohíben copiar entre usuarios distintos o usar automatización de terceros): el producto debe venderse como copia **entre cuentas del mismo titular**. |
| 5 | **Marca** | "TradePilot" ya lo usan otros productos. | Comprobar marca registrada en EUIPO/USPTO y dominio antes de gastar en diseño. Nombre, logo, favicon, colores (la consola ya tiene un tema sólido). |
| 6 | **Soporte y diagnóstico** | Cuando algo falle a un cliente, necesitas datos sin pedirle que abra logs. | Botón *Enviar diagnóstico* (logs, versión, auditoría de las últimas 24 h, sin tokens), reporte de errores automático opt-in (Sentry), página de estado, canal de soporte (correo/Discord) con SLA claro. |
| 7 | **Prueba con NinjaTrader real y matriz de compatibilidad** | Todo se ha probado con simulador y con un addon falso. | Semana de pruebas en cuentas de evaluación reales con Apex/Topstep/Tradeify; documentar versiones de NinjaTrader 8.1.x soportadas; pruebas de reinicio de NinjaTrader, pérdida de red y PC en suspensión. |

## 2. Lo que te diferencia (segunda tanda)

- **SaaS del hub**: el hub alojado por ti, con el coach IA y las noticias servidas desde tu servidor (tú pones la clave de
  IA y cobras el uso en el plan, el cliente no toca `ANTHROPIC_API_KEY`).
- **Onboarding guiado**: asistente de primer arranque (detectar NinjaTrader, instalar addon, elegir maestra, aplicar perfil
  de prop firm, probar avisos). Vídeo de 3 minutos.
- **Inglés** además de español (i18n de la consola y del addon); los prop firms y el mercado grande son en inglés.
- **Más plataformas**: la abstracción `BrokerBridge` permite Tradovate/Rithmic directos sin NinjaTrader. Es la petición
  número uno del público de prop firms.
- **Auto-actualización** del engine y del addon con canal estable/beta, y notas de versión dentro de la consola.
- **Copias de seguridad** automáticas del SQLite y exportación/importación de la configuración.
- **Pruebas en Windows en CI** (hoy CI corre en Linux), pruebas de carga de latencia y una suite de regresión del addon.
- **Landing page** con precios, comparativa frente a copiers existentes, demo en vídeo y lista de espera.

## 3. Modelo de precios sugerido

| Plan | Para quién | Incluye | Precio orientativo |
|---|---|---|---|
| Solo | 1 PC, hasta 3 cuentas | Copia, riesgo, Operar, avisos Telegram/push, calendario | 39 €/mes |
| Pro | 1 PC, cuentas ilimitadas | Solo + trailing IA, coach IA, informes, vista de bolsillo | 79 €/mes |
| Equipo | Varios PCs | Pro + hub alojado, usuarios y roles | 149 €/mes + 20 €/PC |

Prueba de 14 días con todo; descuento anual del 20 %.

## 4. Prompt maestro para tu agente

Copia desde la línea siguiente hasta el final del bloque. Está pensado para que otro agente (Claude Code u otro) genere
material nuevo sabiendo exactamente qué es la app, cómo está hecha y qué reglas no puede romper.

```text
Eres el agente de producto e ingeniería de TradePilot X. Trabaja siempre en español, con criterio profesional y sin
inventar funciones que no existan: todo lo que digas del producto debe salir de esta descripción o del repositorio.

## Qué es TradePilot X
Software para traders de futuros con cuentas de prop firm (Apex, Topstep, Tradeify, MFFU…) que operan en NinjaTrader 8.
Copia en tiempo real las operaciones de una cuenta maestra a varias seguidoras del mismo titular, protege las cuentas
con reglas de riesgo automáticas y permite operar y vigilar todo desde el teléfono. Público: traders que llevan de 2 a
20 cuentas de evaluación y financiadas a la vez y pierden cuentas por errores de tamaño, stops cortos o noticias.

## Arquitectura (monorepo, GitHub Ifermin1/Ifermin1)
- ninjatrader/TradePilotXBridge.cs: addon de NinjaTrader 8 (C#, versión 2.9). Publica eventos por ZeroMQ (puerto 5556:
  fills, posiciones, precios con volumen, heartbeat) y atiende comandos con confirmación (puerto 5557: órdenes, cancelar,
  modificar, cerrar, brackets OCO). Se compila en NinjaTrader con F5.
- engine/ (Python 3.11, FastAPI, SQLite, asyncio): el motor. Corre en el PC de trading junto a NinjaTrader. Servicios:
  replicación (copia con multiplicador, modo mercado o límite, tolerancia, desincronización e igualado), riesgo (pérdida
  diaria, objetivo, drawdown trailing por prop firm, kill switch, día cerrado, perfiles automáticos, presets, tope de
  contratos), operar manual (órdenes idempotentes, OCO, breakeven, cierre parcial, plantillas), trailing stop (fijo,
  adaptativo por ATR y volumen, IA), calendario económico con análisis, recomendaciones, coach IA (revisión de sesión,
  semáforo de entrada, chat con los datos), avisos (Telegram con órdenes /estado /cerrar…, Web Push), informe semanal y
  CSV, reporter al hub con cola local. API REST con token Bearer y WebSocket. Tests con pytest (124).
- web/ (Vite, React, TypeScript, PWA): la consola, servida por el propio engine. Páginas: Inicio, Operar, Calendario,
  Análisis, Cuentas, Riesgo, Auditoría y vista de bolsillo (#pocket). Instalable en el teléfono; acceso remoto por túnel
  HTTPS (scripts/publish.ps1). Tema oscuro/claro, español.
- hub/ (FastAPI + SQLite): servidor central opcional que reúne varios engines (solo lectura, fase 1).
- IA: Anthropic Claude (modelo por defecto claude-opus-5-5) para análisis de noticias, trailing IA y coach; sin clave,
  todo funciona por reglas.
- Operación: scripts/update.ps1 actualiza e instala; docs/GUIA.md (manual de usuario), docs/ARQUITECTURA.md,
  docs/COMERCIALIZACION.md (esta hoja de ruta). CI en GitHub Actions (pytest, build web, escenarios con NinjaTrader
  simulado).

## Reglas que no se pueden romper
1. El camino de la copia nunca depende de Internet ni de la IA: el engine vive junto a NinjaTrader y sigue copiando sin
   consola, sin hub y sin clave de IA.
2. Ninguna función nueva puede meter latencia en la copia ni en las órdenes manuales (idempotentes por client_id,
   confirmadas por NinjaTrader, nunca duplicadas).
3. Cerrar posiciones y cancelar órdenes tienen que funcionar siempre, incluso con licencia caducada o kill switch activo.
4. Dinero real: cada acción destructiva pide confirmación y queda en la auditoría; el coach y la IA son orientativos y
   así debe decirse (no es asesoramiento financiero).
5. Copia solo entre cuentas del mismo titular; nunca describir el producto como copia entre usuarios distintos
   (reglas de los prop firms).
6. Todo en español salvo que se pida inglés; documentación para un usuario que no es técnico; nombres de archivos y
   rutas reales del repositorio.

## Lo que falta para comercializar (prioridad)
1) instalador de Windows firmado con servicio y copia del addon; 2) licencias por máquina con prueba de 14 días y cobro
en Stripe (planes Solo 39 €, Pro 79 €, Equipo 149 €/mes); 3) usuarios con contraseña, 2FA y roles; 4) términos, privacidad,
aviso de riesgo y revisión de las reglas de copy trading de cada prop firm; 5) marca y dominio (comprobar que
"TradePilot" está libre); 6) diagnóstico y soporte (enviar logs, Sentry, página de estado); 7) pruebas con NinjaTrader
real y matriz de compatibilidad. Después: hub alojado como SaaS con la IA incluida, onboarding guiado, inglés, más
plataformas (Tradovate, Rithmic), auto-actualización, copias de seguridad, landing con precios.

## Cómo trabajar
- Antes de proponer, lee docs/GUIA.md, docs/ARQUITECTURA.md y el código afectado. Cita rutas reales.
- Cada entrega: qué archivos cambian, qué se probó (tests, build, escenario) y qué decisiones necesitan revisión del
  dueño. Commits pequeños en una rama, CI en verde antes de dar algo por hecho.
- Si una tarea toca dinero, licencias o datos personales, enumera primero los riesgos y pide confirmación.

## Tarea
[DESCRIBE AQUÍ LO QUE QUIERES: p. ej. "redacta la landing page con precios", "diseña el sistema de licencias y haz un
plan de implementación en el engine", "escribe los términos de servicio y el aviso de riesgo", "crea el asistente de
primer arranque en la consola", "prepara el instalador de Windows".]
```
