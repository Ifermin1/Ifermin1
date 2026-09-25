# TradePilot X — Guía paso a paso

Todo lo que hay que hacer, en orden, para instalar, operar y reaccionar ante incidentes. Los comandos de PowerShell se
ejecutan en el PC de trading (donde corre NinjaTrader), en una ventana normal de PowerShell salvo que se indique lo contrario.

---

## 1. Instalación inicial (una sola vez)

**Requisitos:** Windows con NinjaTrader 8, Python 3.11+, Node.js 18+ y Git instalados. El addon necesita `NetMQ.dll` y
`AsyncIO.dll` en `Documents\NinjaTrader 8\bin\Custom` y añadidas como referencias en el editor NinjaScript (ya lo tienes de la
versión de escritorio).

1. Clonar el proyecto en tu carpeta de usuario (no en `C:\WINDOWS\system32`):
   ```powershell
   cd $HOME
   git clone -b claude/eager-cannon-n4krnv https://github.com/Ifermin1/Ifermin1 tradepilot
   cd tradepilot
   ```
2. Abrir el puerto en el firewall para poder entrar desde el teléfono (PowerShell **como administrador**, una vez):
   ```powershell
   New-NetFirewallRule -DisplayName "TradePilot X" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000
   ```
3. Primer arranque (crea el entorno de Python, compila la consola web, crea `engine\.env`):
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\run_engine.ps1
   ```
4. Pararlo con Ctrl+C y editar `engine\.env` con el Bloc de notas:
   - `ENGINE_MODE=ninja`
   - `API_TOKEN=` un token largo tuyo (es la única llave de la consola).
5. Instalar el addon en NinjaTrader: `scripts\update.ps1` copia `ninjatrader\TradePilotXBridge.cs` a la carpeta de AddOns.
   Después, en NinjaTrader: *New → NinjaScript Editor → F5* (compilar) y **reiniciar NinjaTrader**.
   En *New → NinjaScript Output* debe aparecer `[TradePilotX] Bridge v2.6 online`.
   **Quitar los avisos de voz de NinjaTrader**: por defecto reproduce "order filled", "order cancelled"… por cada orden de
   cada cuenta; con varias seguidoras son decenas de avisos por operación y frenan a NinjaTrader. En *Tools → Options →
   General*, apartado *Sounds*, desmarca todos los sonidos de órdenes (order filled, order cancelled, order pending, order
   rejected, position closed…). Deja si quieres el de *connection lost*.
6. Arrancar el engine: `powershell -ExecutionPolicy Bypass -File scripts\run_engine.ps1`. Imprime las URLs de la consola.
7. (Recomendado) Arranque automático con Windows y reinicio si se cae, PowerShell **como administrador**, una vez:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
   ```
   A partir de ahí el engine arranca solo al iniciar sesión (minimizado; los logs quedan en `engine\logs\`). Para arrancarlo o
   pararlo a mano: `Start-ScheduledTask -TaskName 'TradePilotX Engine'` / `Stop-ScheduledTask -TaskName 'TradePilotX Engine'`.
   Con la tarea instalada, para actualizar: `Stop-ScheduledTask`, `update.ps1` (arranca una copia en la ventana; ciérrala con
   Ctrl+C al terminar) y `Start-ScheduledTask`.

---

## 2. Actualizar a una versión nueva

Con el engine parado (Ctrl+C):

```powershell
cd $HOME\tradepilot
powershell -ExecutionPolicy Bypass -File scripts\update.ps1
```

Descarga el código, copia el addon si cambió (con copia de seguridad del anterior) y arranca el engine. **Si el script dice
que copió el addon**, hay que compilarlo: *NinjaScript Editor → F5* y reiniciar NinjaTrader. Comprueba la versión en el
Output de NinjaTrader y en *Inicio* de la consola ("addon vX.Y"). Si la consola muestra "Addon desactualizado" en rojo, falta
ese paso.

---

## 3. Entrar en la consola

- **En el PC:** `http://localhost:8000`. Token: el `API_TOKEN` del `.env`.
- **En el teléfono (misma Wi-Fi):** la URL `http://192.168.x.x:8000` que imprime el engine al arrancar. En Android, menú de
  Chrome → *Añadir a pantalla de inicio*; en iOS, compartir en Safari → *Añadir a pantalla de inicio*. Queda como app.
- Acceso desde fuera de casa: fase posterior (túnel), ver `docs/ARQUITECTURA.md` §2.

### La consola de un vistazo

- **Barra de estado** (arriba en el PC, fija sobre las pestañas en el móvil): P&L del día de las cuentas en juego, cuántas
  seguidoras copian, posiciones abiertas y el botón **DETENER** (kill switch con cierre de posiciones). El símbolo `≈` indica
  que el P&L se está estimando con el último precio del addon; sin `≈` es el dato tal cual lo reporta NinjaTrader.
- **Vista compacta**: el botón ▥ de la esquina superior derecha reduce márgenes y tamaños para ver más cuentas en pantalla
  (ideal en el teléfono). Se recuerda en ese navegador.
- **Inicio** es la sala de control: avisos rojos, indicadores del día, el **gráfico de P&L** (una línea por cuenta; rangos
  `1h`, `4h`, `hoy`; toca el gráfico para ver los valores a una hora; pulsa una cuenta de la leyenda para ocultarla; *Tabla*
  muestra las mismas cifras cada 5 minutos), la tarjeta **Calidad de ejecución** (¿entran todas las seguidoras al precio del
  maestro? porcentaje al mismo precio, deslizamiento medio en ticks, tiempo en bróker, la peor seguidora; un gráfico por
  operación con un punto por seguidora y otro por seguidora con su media; los botones *Entrar al precio del maestro* /
  *Entrar a mercado* cambian de golpe el modo de entrada de todas las seguidoras; ver `docs/PLAN_MISMO_PRECIO.md`) y las
  **cuentas en juego**: maestra y seguidoras que copian, cada una con su P&L,
  posición (con distancia al mercado en puntos y dólares), **stops y take profits vivos** con su distancia al precio,
  último fill o rechazo y el botón *Cerrar*.
- **Cuentas** muestra los mismos paneles con los mandos de copia (multiplicador, interruptor, ⚙ opciones de ejecución).
- **Auditoría** filtra por texto, cuenta, familia (*Copia*, *Bloqueos*, *Errores*, *Riesgo*, *Sistema*) o *Solo importantes*;
  *Copiar* y *.txt* exportan exactamente lo filtrado.

---

## 4. Configurar las cuentas (cada vez que cambie la operativa)

1. **Cuentas → Cuenta maestra.** Se detecta sola del addon. Para cambiarla, elige otra en el desplegable y confirma: el addon
   la guarda y sobrevive a reinicios. Al cambiar de maestra, los vínculos anteriores no se arrastran.
2. **Seguidoras.** Aparecen solas las cuentas conectadas en NinjaTrader; las desconectadas se ocultan solas. Para cada
   seguidora: multiplicador (x1 = mismos contratos) e interruptor *copiando*. La ✕ elimina el vínculo.
3. **Gestionar cuentas** (botón arriba a la derecha): todas las que NinjaTrader conoce, con buscador y filtros. Alias (por
   ejemplo "Eval MFF 172"), activar/desactivar a mano (una desactivada nunca recibe copias), y "auto" para volver a la regla
   "activa mientras está conectada".
4. **Riesgo → Límites por cuenta**, para cada cuenta de evaluación (en la tabla, *Editar* carga la fila en el formulario y
   *Quitar* elimina los límites de esa cuenta):
   - *Posición máx. (contratos)*: contratos máximos por orden **y** de posición resultante. Ponlo al máximo que permita el prop
     firm (MFF: 3 en NQ).
   - *Pérdida diaria máx. ($)*: al llegar, la cuenta se pausa y se cierra sola. Ponlo **por debajo** del límite de la
     evaluación (si la eval permite -1.000, pon 800).
   - **Comisiones** (tarjeta *Comisiones* en *Riesgo*): NinjaTrader reporta el P&L **bruto** en las cuentas de prop firm. El
     engine suma por cada contrato ejecutado (entrada y salida cuentan cada una) la tarifa por contrato y lado del símbolo
     (por defecto NQ/ES 2,0, MNQ/MES 0,5; pon las de tu bróker y añade símbolos). Cada tarjeta muestra el **P&L neto**, y
     debajo bruto, comisiones y contratos del día; la barra superior también va en neto. El objetivo de ganancia y la
     pérdida diaria se miden sobre el neto. Si NinjaTrader ya descuenta comisiones (plantilla en *Tools → Commissions*),
     desmarca *descontar comisiones* para no restarlas dos veces. El contador se reinicia a la hora de cierre
     (`DRAWDOWN_EOD_TIME`) y sobrevive a reinicios del engine.
   - *Ganancia diaria máx. ($)*: se mide en **neto** (bruto − comisiones). Al llegar, la cuenta se pausa y se cierra sola para **asegurar la ganancia** (útil para
     reglas de consistencia del prop firm o para no devolver lo ganado). 0 = sin objetivo. Avisa al 80 % y, como con la
     pérdida, no se reanuda mientras el P&L del día siga por encima del objetivo (súbelo o quítalo si de verdad quieres seguir).
   - *Drawdown máx. del prop firm ($)* y *Tipo de drawdown*. El engine lleva por cada cuenta el **máximo** que llegó a valer
     (guardado entre reinicios) y calcula el **suelo** = máximo − drawdown; el suelo nunca baja. Hay dos tipos, elige el de tu
     evaluación:
     - **Dinámico (intradía)**: el máximo sube tick a tick con el flotante (APEX trailing, MFF y la mayoría). Si vas +1.000
       flotante y vuelves a 0, el suelo ya subió 1.000.
     - **EOD (cierre del día)**: el máximo solo se actualiza con el **balance al cierre del día** (APEX EOD, Topstep MLL).
       Durante el día el suelo no se mueve aunque la cuenta suba; lo flotante no cuenta para el máximo. La hora de cierre es
       `DRAWDOWN_EOD_TIME` en `engine\.env` (hora local del PC; por defecto 17:00; APEX y Topstep cierran a las 17:00 hora
       de Nueva York, ajústala a tu zona).
     En los dos tipos la **caída se vigila en tiempo real con el flotante**: aviso al 80 % (`DRAWDOWN_WARNING`) y, cuando
     faltan los dólares de *Cerrar cuando falten* (colchón) para el suelo, pausa y cierre (`DRAWDOWN_LIMIT`) **antes** de que
     el prop firm cierre la cuenta. Pon el drawdown real del prop firm (APEX 50K: 2500) y un colchón de 100 a 200 $: el prop
     firm mide tick a tick y el engine cada 2 s. *El suelo se bloquea en*: para planes en que el suelo deja de subir al llegar
     a inicial + 100 (APEX 50K → 50100); 0 = sube siempre.
   - **Varias cuentas a la vez**: en la tabla de límites marca las casillas (o la de la cabecera) y usa *Pausar / Reanudar /
     Quitar marcadas*; el botón *Aplicar a las N marcadas* del formulario guarda los mismos límites en todas las marcadas
     (sobrescribe todos sus límites). En *Cuentas → Gestionar cuentas* las casillas permiten *Activar / Desactivar / Modo auto /
     Olvidar marcadas*. El desplegable de cuenta del formulario solo ofrece las activas.
   - La tarjeta **Drawdown dinámico** muestra por defecto solo las cuentas activas (*Con límite* y *Todas* arriba a la derecha), cuánto vale cada cuenta, su máximo, el drawdown
     actual, el suelo y el margen. *Ajustar máximo* fija el máximo a mano (si el prop firm tiene otro porque la cuenta operó sin el
     engine) o, vacío, lo reinicia al valor actual (evaluación nueva). Cada panel de cuenta en *Inicio* y *Cuentas* lleva la misma fila.
5. **Riesgo → Horario** (recomendado para prop firms): *Copiar desde* (por ejemplo 09:30) y *Cerrar todo a las* (por ejemplo
   15:55, hora local del PC). A esa hora se cancela todo, se cierra todo y no se copia hasta el día siguiente. "Incluir la
   maestra" marcado.
6. **Opciones de ejecución por seguidora** (⚙ en su tarjeta de *Cuentas*):
   - *Símbolo destino*: vacío = el mismo contrato que la maestra. `MNQ` = la seguidora opera el micro; combínalo con el
     multiplicador (maestra 1 NQ → seguidora 10 MNQ con ×10). La detección de desincronización ya lo tiene en cuenta.
   - *Entrada*: "A mercado" copia al instante (más deslizamiento en mercados rápidos). "Límite al precio del maestro ± ticks"
     manda la entrada como límite al precio de fill del maestro más la tolerancia; si no se llena en la *Espera*, "A mercado
     lo que falte" o "Cancelar (no entrar)". Con "Cancelar", la seguidora puede quedarse fuera de una operación (aparece
     `ENTRY_MISSED`). Las salidas van siempre a mercado o con su propia orden. Requiere addon v1.7.
7. **Copiar** (pestaña): solo para reglas finas con filtro de símbolo (por ejemplo copiar únicamente `NQ`).

---

## 5. Operativa diaria

1. Abrir NinjaTrader y conectar la cuenta maestra y las seguidoras. Comprobar en el Output `Bridge v2.6 online`.
2. Arrancar el engine (`run_engine.ps1`) si no está corriendo. En *Inicio*: modo `NinjaTrader · addon v2.6`, heartbeat
   actualizándose, sin avisos rojos.
3. En *Cuentas*: las seguidoras que deben copiar con el interruptor encendido y el badge *copiando*.
4. Operar solo en la cuenta maestra. Todo (entrada, stop, take profit, modificaciones, cancelaciones) se copia.
5. Vigilar *Inicio*: en *Cuentas en juego* cada seguidora debe tener la misma posición que la maestra (× multiplicador) y sus
   stops/TPs vivos; la *Actividad reciente* debe mostrar, por cada operación, `MASTER_RECEIVED` → `REPLICATED` →
   `FOLLOWER_FILL`. Las tarjetas de latencia y deslizamiento indican la calidad de la copia y el gráfico, cómo va el día.
6. Al terminar: el cierre programado lo hace solo; si no lo usas, comprueba que todas las cuentas están planas antes del
   cierre del prop firm.

---

## 6. Qué significa cada aviso y qué hacer

| Aviso / evento | Significado | Qué hacer |
|---|---|---|
| **DESINCRONIZADA** (Cuentas, rojo) | La seguidora no tiene la posición de la maestra × multiplicador desde hace más de 6 s. Solo se le copian salidas. | Pulsa *Igualar a la maestra* (manda la diferencia a mercado) o *Cerrar* esa cuenta. |
| `FOLLOWER_REJECTED` | El bróker rechazó una orden copiada; el mensaje trae el motivo (límite de contratos, margen…). | Revisa límites del prop firm y el *Tamaño máx.* en *Riesgo*. Si era un stop, el engine ya cerró la cuenta (`NAKED_CLOSE`). |
| `FOLLOWER_REJECTED` "maximum order quantity … Rule #4304" | APEX limita a 10 contratos la suma de la orden nueva **más todas las órdenes vivas** de la cuenta. Con varios stops de 2 para una misma posición, cualquier orden extra (incluso un cierre) se rechaza. | Un solo stop y un solo TP por posición en la maestra; pon *Tamaño máx.* en *Riesgo* por debajo del límite del prop firm. |
| `ACCOUNT_LOCKED` | El bróker rechaza **todas** las órdenes de esa cuenta ("Order can be placed by administrators only"): el prop firm la ha bloqueado. El engine la desactiva para no seguir mandándole copias. | Hablar con el prop firm. Cuando vuelva a aceptar órdenes, activarla de nuevo en *Cuentas* (el interruptor). |
| `REPLICATED … addon: la copia sigue viva: se ejecuta sola…` | Saltó el stop/TP de la maestra y la seguidora tiene su propia copia al mismo precio, que está saltando en ese instante. El addon 2.6 no manda nada a mercado: la deja ejecutarse. Solo si en 750 ms sigue sin llegar a lo que ejecutó la maestra, la cancela y manda el resto a mercado. | Nada. Es lo normal desde 2.6 (antes mandaba 1 a mercado "para seguirle" y salían dos ventas). |
| `NAKED_CLOSE` | Un stop copiado fue rechazado y el engine cerró esa cuenta para no dejarla sin protección. | Comprobar en NinjaTrader que quedó plana. |
| `FOLLOWER_FILL … (maestro 29459.0, +1.0, 176 ms; en bróker 270 ms: engine 6 + addon 14 + bróker 250)` | Copia ejecutada. El primer tiempo es lo que tardó el engine en enterarse (incluye la cola de mensajes); *en bróker* es el tiempo real entre el fill de la maestra y el de la seguidora según el reloj de NinjaTrader, desglosado en lo que tardó el engine en decidir, el addon en enviar y confirmar, y el bróker en llenar. | Si *engine* o *addon* pasan de ~30 ms, avisa (algo va lento en el PC). Si *bróker* pasa de ~300 ms, es la conexión con Rithmic/el bróker: un VPS cerca de Chicago lo baja. |
| `OVERCLOSE_FIX` | La seguidora quedó con posición contraria (o con posición y la maestra plana) justo después de una copia: el bróker llenó una copia que ya había dado por cancelada. El engine la cerró a mercado (el addon 2.4 lo corrige antes por su cuenta). | Comprobar en NinjaTrader que quedó plana. Si ves varios seguidos con addon < 2.6, actualiza el addon: era la orden extra "para seguirle" del fill parcial. |
| `PHANTOM_ORDER` | Una orden viva sin nada por ejecutar (en el gráfico sale con 0 contratos). No protege nada. El addon 2.4 cancela las copias TPX; las de la maestra no se tocan. | Si sigue ahí, cancélala a mano en NinjaTrader. |
| **Límite de pérdida diaria alcanzado** | La cuenta se pausó y se cerró. | Nada hasta mañana. *Reanudar* solo funciona si el P&L ya no está por debajo del límite. |
| `DRAWDOWN_WARNING` | La cuenta consumió el 80 % del drawdown dinámico permitido: está cerca del suelo que el prop firm usa para cerrarla. | Reducir o cerrar. El engine cerrará solo al llegar al colchón configurado. |
| **Límite de drawdown alcanzado** (`DRAWDOWN_LIMIT`) | A la cuenta le quedaban el colchón o menos hasta el suelo: se pausó y se cerró antes de que el prop firm la cerrara. | *Reanudar* solo funciona si vuelve a tener margen (sube el límite, baja el colchón o ajusta el máximo si de verdad quieres seguir). |
| `PEAK_SET` | Alguien fijó o reinició a mano el máximo del drawdown de una cuenta. | Nada; comprobar que coincide con el que tiene el prop firm. |
| **Objetivo de ganancia diaria alcanzado** | La cuenta se pausó y se cerró con la ganancia asegurada (medida en neto: el mensaje trae bruto y comisiones). | Nada hasta mañana. *Reanudar* solo funciona si subes o quitas el objetivo. |
| **Sesión cerrada por horario** | Se ejecutó el cierre programado. | Nada. Si de verdad hay que seguir hoy, *Reabrir hoy* en *Riesgo*. |
| **Sin heartbeat del addon** | NinjaTrader no envía eventos desde hace más de 15 s. No se copia. El engine reconecta solo el canal de eventos (`RESUBSCRIBE`); si tampoco responde a comandos aparece `ADDON_DOWN`. | Si no se recupera en un minuto: comprobar NinjaTrader abierto, conectado y el addon cargado (Output). |
| `ERROR` "NinjaTrader no confirmó la orden" | El addon no respondió a una orden en 6 s (dos intentos). La orden **no** se ejecutó. | Revisar NinjaTrader (addon cargado, Output). Si la maestra tiene posición, la seguidora saldrá DESINCRONIZADA: *Igualar* o *Cerrar*. |
| `RESUBSCRIBE` / `ADDON_RECOVERY` | El addon se reinició (recompilar, reconectar) o publicaba eventos que no llegaban; el engine reconectó el canal en unos segundos y volvió a registrar las seguidoras. Con addon v1.8 la detección tarda ~4 s; con uno anterior, 15 s. | Mirar *Cuentas*: si la maestra operó mientras tanto, la seguidora sale DESINCRONIZADA → *Igualar* o *Cerrar*. |
| **Mensajes perdidos del addon** | Se perdieron eventos entre NinjaTrader y el engine. | Revisar *Cuentas*: si hay desincronización, igualar. Si se repite, avisar. |
| `BLOCKED` | Copia bloqueada por kill switch, horario, pausa, límite de tamaño o desincronización. El mensaje dice cuál. | Es la protección actuando. Revisar el motivo. |
| `BLOCKED` "Salida no copiada: no tiene posición que proteger" | La maestra puso un stop/TP de una entrada que la seguidora no tiene (p. ej. entrada bloqueada por tamaño). Copiarlo abriría posición contraria al saltar. | Nada: la seguidora conserva los stops de lo que sí tiene. Las cancelaciones se copian siempre. |
| `TRIMMED` | Una salida se copió por menos contratos: lo que le quedaba por proteger o por cerrar a la seguidora. Un cierre nunca invierte la posición. | Nada. |
| `SKIPPED` "Cierre del maestro no copiado" | La maestra cerró y la seguidora no tenía posición (ya estaba cerrada). Una salida nunca abre una posición nueva. | Nada. |
| `NO_RULE` | Operación de la maestra sin regla que la copie. | Revisar el maestro de la regla o el filtro de símbolo. |
| **Addon desactualizado** | El addon compilado es anterior al que exige el engine. | Paso 2 (actualizar y compilar). |
| `ENTRY_MISSED` | Una entrada límite con tolerancia no se llenó en el plazo y estaba configurada para cancelar. La seguidora no tiene esa operación. | Decidir si entrar a mano o subir la tolerancia / usar "a mercado lo que falte". |
| `SKIPPED` "cierre de emergencia de la maestra" | Durante 20 s tras cerrar la maestra por emergencia, sus fills no se copian. | Nada; es la protección contra salidas dobles. |
| `ENTRY_MODE_SET` | Alguien pulsó *Entrar al precio del maestro* o *Entrar a mercado* en *Inicio*: cambió el modo de entrada de todas las seguidoras. | Nada. Comprueba en *Cuentas → ⚙* si quieres afinar tolerancia o espera por cuenta. |

---

## 7. Emergencias

- **Parar todo ahora:** *Riesgo → DETENER TODO* con "y cerrar posiciones" e "incluida la maestra" marcados. Bloquea todas las
  copias y cierra todas las cuentas a mercado. Para reanudar, *Reanudar replicación*.
- **Cerrar una sola cuenta:** *Cuentas → Cerrar* en esa tarjeta.
- **Cerrar seguidoras sin tocar la maestra:** *Riesgo → Cerrar todas las seguidoras*.
- **El engine se cayó o el PC se reinició:** las posiciones y órdenes siguen en el bróker. Arrancar NinjaTrader y luego el
  engine; el addon recupera las órdenes vivas y el engine vuelve a comparar posiciones (si algo no cuadra, aparece
  DESINCRONIZADA). Antes de operar, mirar *Cuentas*.
- **Nada funciona y hay posición abierta:** cerrar a mano en NinjaTrader (*Close* en cada cuenta). TradePilot nunca sustituye
  esa opción.

---

## 8. Ante un incidente: qué recopilar

1. La captura de *Inicio* y de *Cuentas* de la consola.
2. La *Auditoría* como texto: en esa pestaña, filtra si quieres (cuenta, tipo, familia o *Solo importantes*) y pulsa
   **⧉ Copiar** (portapapeles) o **⤓ .txt**
   (archivo). Sale en orden cronológico con hora, tipo, origen → destino y mensaje. El icono ⧉ de cada fila copia solo
   esa línea.
3. El *NinjaScript Output* completo desde la última línea `Bridge vX.Y online`.
4. El archivo `engine\data\journal\AAAA-MM-DD.jsonl` del día: contiene cada mensaje recibido y cada orden enviada con hora
   exacta. Con él se reproduce lo ocurrido y se convierte en una prueba automática para que no vuelva a pasar.

---

## 9. Comprobación rápida antes de usar cuentas reales

Con `Sim101 → Sim102` vinculadas:

1. Entrar con 2 contratos a mercado con stop y take profit en Sim101 → Sim102 debe quedar con 2, un solo stop y un solo TP.
2. Mover el stop en Sim101 → se mueve en Sim102 (`Modificada` en el Output).
3. Dejar que salte el stop → Sim102 cierra por su propia orden; una sola venta.
4. Cerrar a mano solo Sim102 en NinjaTrader → a los 6 s aparece DESINCRONIZADA; *Igualar* la devuelve.
5. *DETENER TODO* con posiciones abiertas → todas planas, `FLATTEN` en la auditoría para cada cuenta.

Si los cinco pasos responden así, la configuración está lista para las cuentas de evaluación.
