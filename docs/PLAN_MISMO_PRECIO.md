# Plan: que todas las seguidoras entren al mismo precio, y más rápido

## 1. Dónde se va el tiempo hoy (medido)

Cada `FOLLOWER_FILL` de la auditoría desglosa el tiempo entre el fill de la maestra y el de la seguidora, con el reloj de
NinjaTrader. En la sesión del 17/9 los valores típicos fueron:

| Tramo | Qué es | Medido | Se puede bajar |
|---|---|---|---|
| engine | desde que llega el fill de la maestra hasta que el engine manda el lote de copias | 5-8 ms | poco: ya es un solo lote para todas las seguidoras |
| addon | NinjaTrader recibe el lote, crea y envía las órdenes (en paralelo) y confirma | 10-20 ms | poco: 2.6 quita la búsqueda del instrumento por copia |
| bróker | ida y vuelta de la orden a Rithmic/Tradovate y el fill | 150-300 ms | **sí**: es el 90 % del total y depende de la red y de dónde está el PC |

Conclusión: el copiador ya no es el cuello de botella. Lo que separa el precio de la seguidora del de la maestra es el
tiempo del bróker (el mercado se mueve mientras la orden viaja) y, sobre todo, el **tipo de orden** con que entra la maestra.

## 2. Por qué una entrada a mercado nunca sale exactamente al mismo precio

Si la maestra entra **a mercado**, su fill es un hecho consumado: el engine se entera cuando ya ocurrió y las copias
salen 200-300 ms después. En un NQ moviéndose, 1-2 ticks de diferencia son lo normal (la tarjeta *Calidad de ejecución*
lo muestra operación a operación). Se puede recortar, no eliminar.

Las **salidas** ya entran al mismo precio: el stop y el take profit de la maestra se copian como órdenes propias de cada
seguidora al mismo precio, y saltan a la vez (addon 2.6: nada se manda a mercado mientras la copia esté viva).

## 3. Los tres caminos, de más a menos efecto

### A. La maestra entra con límite (el único camino al precio exacto)

Si la maestra pone su entrada como **orden límite** (o stop de entrada), el engine la copia como orden pendiente
(`ORDER_PENDING`) a cada seguidora **al mismo precio, antes de que se ejecute**. Cuando el mercado toca ese precio, todas
se llenan al mismo precio, sin latencia que valga. Es lo que ya pasa con los stops y TPs.

- Cómo: en NinjaTrader, entrar con *Limit* (o con la ATM en modo límite) en vez de *Market*.
- Coste: a veces la maestra no se llena (el precio no vuelve). Pero si la maestra no entra, ninguna seguidora entra: siguen
  iguales.
- No hace falta cambiar nada en TradePilot X.

### B. Seguidoras con "Entrar al precio del maestro" (botón en *Calidad de ejecución*)

Con la maestra a mercado, cada copia sale como **límite al precio de fill de la maestra ±0 ticks**; si en 2 s no se ha
llenado, lo que falte va a mercado (para no dejar a la seguidora fuera de la operación). Efecto: cuando el precio no se
ha ido (la mayoría de las veces) la seguidora entra al precio exacto; cuando se ha ido, entra 2 s más tarde a mercado.

- Es un botón: aplica a todas las seguidoras de la maestra a la vez (y *Entrar a mercado* deshace).
- Ajuste fino por seguidora en *Cuentas → ⚙*: tolerancia en ticks (1-2 ticks llenan más veces sin esperar), espera y qué
  hacer si no se llena.
- Cuidado con "Cancelar (no entrar)": la seguidora puede quedarse fuera de una operación (`ENTRY_MISSED`).

### C. Bajar el tiempo del bróker

- **VPS cerca de Chicago** (donde están los servidores de Rithmic/CME): el tramo *bróker* baja de ~250 ms a ~50-100 ms.
  Es el cambio con más efecto sobre las entradas a mercado.
- Una sola conexión de datos y de órdenes, sin VPN ni Wi-Fi.
- Menos cuentas por conexión: con 11 seguidoras en una misma conexión Rithmic, el bróker encola las órdenes.

## 4. Cómo medirlo

En *Inicio → Calidad de ejecución*:

- **Al mismo precio**: porcentaje de copias que entraron al precio exacto de la maestra (objetivo: > 80 % con A o B).
- **Deslizamiento medio** en ticks (positivo = peor que la maestra) y **Tiempo en bróker** (mediana en ms).
- **Por operación**: cada punto es una seguidora; la línea vertical, la dispersión entre la mejor y la peor. Los puntos
  ámbar están a más de 2 ticks; `!` marca una seguidora sin fill (rechazada o bloqueada).
- **Por seguidora**: la media de ticks de cada cuenta, la peor arriba, con su tiempo de bróker. Una cuenta
  sistemáticamente peor que el resto suele ser una conexión distinta o un prop firm más lento.
- *Tabla*: las mismas cifras, operación a operación, para comparar cuentas.

## 5. Qué hacer, en orden

1. Actualizar al addon 2.6 (quita las órdenes extra que hacían doble salida y rechazos por cantidad).
2. Pulsar **Entrar al precio del maestro** y mirar durante una sesión el % *Al mismo precio*. Si baja mucho la
   participación (muchos fills tardíos a mercado), subir la tolerancia a 1 tick en *Cuentas → ⚙*.
3. Probar a entrar en la maestra con órdenes límite en las operaciones planificadas: es la única forma de que el precio
   sea idéntico en todas.
4. Si el *Tiempo en bróker* pasa de 200 ms de mediana, mover el PC de trading a un VPS cerca de Chicago.
5. Mantener un solo stop y un solo take profit por posición en la maestra (menos órdenes vivas, sin rechazos por cantidad
   máxima del prop firm).
