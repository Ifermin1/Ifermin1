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
   En *New → NinjaScript Output* debe aparecer `[TradePilotX] Bridge v1.6 online`.
6. Arrancar el engine: `powershell -ExecutionPolicy Bypass -File scripts\run_engine.ps1`. Imprime las URLs de la consola.

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

---

## 4. Configurar las cuentas (cada vez que cambie la operativa)

1. **Cuentas → Cuenta maestra.** Se detecta sola del addon. Para cambiarla, elige otra en el desplegable y confirma: el addon
   la guarda y sobrevive a reinicios. Al cambiar de maestra, los vínculos anteriores no se arrastran.
2. **Seguidoras.** Aparecen solas las cuentas conectadas en NinjaTrader; las desconectadas se ocultan solas. Para cada
   seguidora: multiplicador (x1 = mismos contratos) e interruptor *copiando*. La ✕ elimina el vínculo.
3. **Gestionar cuentas** (botón arriba a la derecha): todas las que NinjaTrader conoce, con buscador y filtros. Alias (por
   ejemplo "Eval MFF 172"), activar/desactivar a mano (una desactivada nunca recibe copias), y "auto" para volver a la regla
   "activa mientras está conectada".
4. **Riesgo → Límites por cuenta**, para cada cuenta de evaluación:
   - *Tamaño máx. por orden*: contratos máximos por orden **y** de posición resultante. Ponlo al máximo que permita el prop
     firm (MFF: 3 en NQ).
   - *Pérdida diaria máx. ($)*: al llegar, la cuenta se pausa y se cierra sola. Ponlo **por debajo** del límite de la
     evaluación (si la eval permite -1.000, pon 800).
5. **Riesgo → Horario** (recomendado para prop firms): *Copiar desde* (por ejemplo 09:30) y *Cerrar todo a las* (por ejemplo
   15:55, hora local del PC). A esa hora se cancela todo, se cierra todo y no se copia hasta el día siguiente. "Incluir la
   maestra" marcado.
6. **Copiar** (pestaña): solo para reglas finas con filtro de símbolo (por ejemplo copiar únicamente `NQ`).

---

## 5. Operativa diaria

1. Abrir NinjaTrader y conectar la cuenta maestra y las seguidoras. Comprobar en el Output `Bridge v1.6 online`.
2. Arrancar el engine (`run_engine.ps1`) si no está corriendo. En *Inicio*: modo `NinjaTrader (ZMQ) · addon v1.6`, heartbeat
   actualizándose, sin avisos rojos.
3. En *Cuentas*: las seguidoras que deben copiar con el interruptor encendido y el badge *copiando*.
4. Operar solo en la cuenta maestra. Todo (entrada, stop, take profit, modificaciones, cancelaciones) se copia.
5. Vigilar *Inicio*: la fila de *Actividad reciente* debe mostrar, por cada operación, `MASTER_RECEIVED` → `REPLICATED` →
   `FOLLOWER_FILL`. Las tarjetas de latencia y deslizamiento indican la calidad de la copia.
6. Al terminar: el cierre programado lo hace solo; si no lo usas, comprueba que todas las cuentas están planas antes del
   cierre del prop firm.

---

## 6. Qué significa cada aviso y qué hacer

| Aviso / evento | Significado | Qué hacer |
|---|---|---|
| **DESINCRONIZADA** (Cuentas, rojo) | La seguidora no tiene la posición de la maestra × multiplicador desde hace más de 6 s. Solo se le copian salidas. | Pulsa *Igualar a la maestra* (manda la diferencia a mercado) o *Cerrar* esa cuenta. |
| `FOLLOWER_REJECTED` | El bróker rechazó una orden copiada; el mensaje trae el motivo (límite de contratos, margen…). | Revisa límites del prop firm y el *Tamaño máx.* en *Riesgo*. Si era un stop, el engine ya cerró la cuenta (`NAKED_CLOSE`). |
| `NAKED_CLOSE` | Un stop copiado fue rechazado y el engine cerró esa cuenta para no dejarla sin protección. | Comprobar en NinjaTrader que quedó plana. |
| **Límite de pérdida diaria alcanzado** | La cuenta se pausó y se cerró. | Nada hasta mañana. *Reanudar* solo funciona si el P&L ya no está por debajo del límite. |
| **Sesión cerrada por horario** | Se ejecutó el cierre programado. | Nada. Si de verdad hay que seguir hoy, *Reabrir hoy* en *Riesgo*. |
| **Sin heartbeat del addon** | NinjaTrader no envía nada desde hace más de 30 s. No se copia. | Comprobar NinjaTrader abierto, conectado y el addon cargado (Output). |
| **Mensajes perdidos del addon** | Se perdieron eventos entre NinjaTrader y el engine. | Revisar *Cuentas*: si hay desincronización, igualar. Si se repite, avisar. |
| `BLOCKED` | Copia bloqueada por kill switch, horario, pausa, límite de tamaño o desincronización. El mensaje dice cuál. | Es la protección actuando. Revisar el motivo. |
| `NO_RULE` | Operación de la maestra sin regla que la copie. | Revisar el maestro de la regla o el filtro de símbolo. |
| **Addon desactualizado** | El addon compilado es anterior al que exige el engine. | Paso 2 (actualizar y compilar). |

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
2. El *NinjaScript Output* completo desde la última línea `Bridge vX.Y online`.
3. El archivo `engine\data\journal\AAAA-MM-DD.jsonl` del día: contiene cada mensaje recibido y cada orden enviada con hora
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
