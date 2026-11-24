# JLines Bot · BingX → Telegram

Escanea los pares spot contra USDT de BingX con la lógica del indicador
«Ultimate JLines & MTF EMA» (gráfico de 5 minutos), envía señales detalladas a
Telegram y, si lo activas, opera en spot.

## Archivos

| Archivo | Función |
|---|---|
| `bot.py` | Bucle principal: escaneo, alertas, compras, ventas y latidos |
| `strategy.py` | Reglas de entrada y salida |
| `indicators.py` | EMA, VWAP diario y ATR de Wilder |
| `exchange.py` | Conexión con BingX (ccxt) |
| `notifier.py` | Mensajes de Telegram |
| `config.py` | Lectura de variables de entorno |
| `state.py` | Posiciones abiertas y cooldowns guardados en JSON |
| `requirements.txt`, `Procfile`, `railway.json` | Despliegue en Railway |
| `.env.example`, `.gitignore` | Variables de referencia y exclusiones de Git |

## Reglas de la señal

**LONG (compra)**

- Disparador: la EMA9 cruza al alza el VWAP entre las dos últimas velas de 5 minutos cerradas.
- Obligatorias: JLines 5m alcista (EMA72 > EMA89) y JLines 1h alcista (EMA72 > EMA89).
- Puntuación sobre 7 (mínimo `MIN_SCORE`, 5 por defecto): precio sobre EMA200 en 5m, 15m y 1h; nube 5/15 alcista (EMA5 > EMA15); precio sobre VWAP.
- Stop = precio − `STOP_ATR_MULT` × ATR(14). Objetivo = precio + `TP_R_MULT` × riesgo.

**SHORT (solo alerta).** Es la versión simétrica. En spot no se opera en corto, así que solo se avisa. Puedes silenciarla con `NOTIFY_SHORTS=false`.

**Salidas (solo en modo `trade`)**

- Stop u objetivo: se comprueban con el mínimo o máximo de cada vela cerrada y con el precio actual cada `MONITOR_SECONDS`.
- Cruce bajista de la EMA9 bajo el VWAP al cierre de una vela de 5 minutos.

## Puesta en marcha

1. **Telegram.** Habla con @BotFather, crea el bot y copia el token. Escribe un mensaje a tu bot y abre `https://api.telegram.org/bot<TOKEN>/getUpdates` para ver tu `chat.id`.
2. **BingX (solo si vas a operar).** Crea una API key con permiso de **Spot Trading** y **sin** permiso de retiros. Si usas una IP fija de Railway, puedes restringir la key a esa IP.
3. **GitHub.** Descomprime el zip y sube todos los archivos a la raíz del repositorio (Add file → Upload files). No subas carpetas ni el archivo `.env`.
4. **Railway.** New Project → Deploy from GitHub repo → elige el repositorio. En la pestaña Variables añade las de `.env.example` con tus valores (puedes usar el editor RAW). Deja `MODE=signals` al principio. Para analizar también los pares de bajo volumen, pon `MIN_QUOTE_VOLUME=0`.
5. **Volumen (imprescindible para operar).** Añade un Volume al servicio con mount path `/data`. Railway define `RAILWAY_VOLUME_MOUNT_PATH` y el bot guarda el estado en `/data/state.json`, así que sobrevive a reinicios y redespliegues.
6. Revisa los logs del deploy. Debe llegar a Telegram el mensaje «Bot iniciado» y, cada hora, un «Escaneo OK».

## Pasar a operar

Cuando las señales coincidan con tu gráfico durante varias semanas, pon `MODE=trade`, un `TRADE_USDT` pequeño (10–20 USDT) y `MAX_POSITIONS=1`. Observa las primeras compras y ventas y comprueba que el saldo de BingX cuadra con lo que anuncia el bot.

## Probar en tu PC (opcional)

```bash
pip install -r requirements.txt
cp .env.example .env    # rellena las variables
python -u bot.py
```

## Limitaciones

- No está backtesteado. Las señales no garantizan resultados y no son consejo financiero.
- El VWAP se reinicia a medianoche UTC, mientras que TradingView lo reinicia según la sesión del gráfico. Puede haber pequeñas diferencias.
- Las EMA se calculan sobre 500 velas, no sobre todo el historial. La diferencia es mínima.
- Los stops no son órdenes colocadas en BingX: el bot vende a mercado cuando detecta el nivel, así que en una caída rápida puede salir a un precio peor.
- El PnL que muestra Telegram no incluye comisiones.
- La compra usa un importe en USDT (parámetro `cost` de ccxt). Pruébala primero con un importe pequeño.
- Si BingX cambia su API, actualiza la versión de `ccxt` en `requirements.txt`.
