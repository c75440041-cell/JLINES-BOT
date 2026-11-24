"""JLines Bot: escanea BingX (spot), envía señales a Telegram y opcionalmente opera."""
from __future__ import annotations

import asyncio
import html
import logging
import os
import time

from config import load_config
from exchange import BingXClient
from indicators import TF_MS, candle_open_ms
from notifier import Telegram, fmt, heartbeat_message, signal_message, summary_message
from state import State
from strategy import MIN_BARS, build_signal, exit_reason, prep_5m, prep_htf, trigger

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bot")

CANDLES = 500                      # velas por consulta (la EMA200 necesita historial)
SCAN_PERIOD = TF_MS["5m"] // 1000  # el escaneo se alinea con el cierre de cada vela de 5m
CLOSE_DELAY = 10                   # segundos tras el cierre antes de leer las velas
ERROR_BACKOFF = 120                # segundos sin reintentar una venta fallida


def seconds_to_next_candle() -> float:
    return SCAN_PERIOD - (time.time() % SCAN_PERIOD) + CLOSE_DELAY


async def retry(fn, *args, tries: int = 2):
    for attempt in range(tries):
        try:
            return await fn(*args)
        except Exception:
            if attempt == tries - 1:
                raise
            await asyncio.sleep(1.5)


async def fetch_many(ex, cfg, symbols, tf):
    sem = asyncio.Semaphore(cfg.concurrency)
    out = {}

    async def one(sym):
        async with sem:
            try:
                out[sym] = await retry(ex.ohlcv, sym, tf, CANDLES)
            except Exception as e:
                log.warning("%s %s: %s", sym, tf, e)

    await asyncio.gather(*(one(s) for s in symbols))
    return out


# ---------------- compras y ventas ----------------

async def open_long(ex, cfg, st, tg, s):
    free = await ex.free(cfg.quote)
    if free < cfg.trade_usdt:
        await tg.send(f"⚠️ Saldo insuficiente ({fmt(free)} {cfg.quote}) para {html.escape(s.symbol)}")
        return

    order = await ex.buy_quote(s.symbol, cfg.trade_usdt)
    avg = float(order.get("average") or order.get("price") or 0) or s.price
    qty = float(order.get("filled") or 0) or cfg.trade_usdt / avg
    pos = {
        "qty": qty,
        "entry": avg,
        "stop": avg - (s.price - s.stop),
        "tp": avg + (s.tp - s.price),
        "since": candle_open_ms("5m"),  # solo cuentan las velas posteriores a la entrada
    }
    st.positions[s.symbol] = pos
    st.save()
    await tg.send(
        f"✅ <b>COMPRA ejecutada</b> · {html.escape(s.symbol)}\n"
        f"Cantidad: <code>{fmt(qty)}</code>\n"
        f"Entrada: <code>{fmt(avg)}</code>\n"
        f"Stop: <code>{fmt(pos['stop'])}</code>\n"
        f"Objetivo: <code>{fmt(pos['tp'])}</code>"
    )


async def close_position(ex, st, tg, sym, reason, hint=0.0):
    pos = st.positions[sym]
    res = await ex.sell(sym, pos["qty"])
    if res is None:
        del st.positions[sym]
        st.save()
        await tg.send(f"ℹ️ {html.escape(sym)}: no hay saldo para vender; se quita del registro.")
        return
    order, qty = res
    exit_p = float(order.get("average") or order.get("price") or 0) or hint or pos["entry"]
    pnl = (exit_p - pos["entry"]) / pos["entry"] * 100
    del st.positions[sym]
    st.save()
    await tg.send(
        f"🔚 <b>VENTA</b> · {html.escape(sym)}\n"
        f"Motivo: {html.escape(reason)}\n"
        f"Cantidad: <code>{fmt(qty)}</code>\n"
        f"Salida: <code>{fmt(exit_p)}</code> · PnL aprox. sin comisiones: <b>{pnl:+.2f}%</b>"
    )


async def try_close(ex, st, tg, sym, reason, fail_until, hint=0.0):
    try:
        await close_position(ex, st, tg, sym, reason, hint)
    except Exception as e:
        log.exception("Error vendiendo %s", sym)
        fail_until[sym] = time.time() + ERROR_BACKOFF
        await tg.send(
            f"❌ Error vendiendo {html.escape(sym)}: {html.escape(str(e))[:200]}\n"
            f"Reintento en {ERROR_BACKOFF // 60} min."
        )


async def process_exits(ex, st, tg, d5s, fail_until):
    """Salidas por velas cerradas: stop, objetivo o cruce bajista de EMA9 bajo VWAP."""
    for sym in list(st.positions):
        d5 = d5s.get(sym)
        if d5 is None or time.time() < fail_until.get(sym, 0):
            continue
        reason = exit_reason(d5, st.positions[sym])
        if reason:
            hint = float(d5.iloc[-1]["close"]) if len(d5) else 0.0
            await try_close(ex, st, tg, sym, reason, fail_until, hint)


async def live_exits(ex, st, tg, fail_until):
    """Vigila el precio actual de las posiciones abiertas entre escaneos."""
    for sym in list(st.positions):
        pos = st.positions.get(sym)
        if pos is None or time.time() < fail_until.get(sym, 0):
            continue
        try:
            price = await ex.price(sym)
        except Exception as e:
            log.warning("Precio de %s: %s", sym, e)
            continue
        if price <= 0:  # precio no válido: nunca vender por eso
            continue
        if price <= pos["stop"]:
            reason = f"Stop alcanzado en tiempo real (precio {fmt(price)})"
        elif price >= pos["tp"]:
            reason = f"Objetivo alcanzado en tiempo real (precio {fmt(price)})"
        else:
            continue
        await try_close(ex, st, tg, sym, reason, fail_until, price)


# ---------------- señales ----------------

async def dispatch(ex, cfg, st, tg, signals):
    now = time.time()
    detailed, extra = 0, []
    for s in signals:
        if s.side == "SHORT" and not cfg.notify_shorts:
            continue
        key = f"{s.symbol}:{s.side}"
        if now - st.last_signal.get(key, 0) < cfg.cooldown_min * 60:
            continue
        st.last_signal[key] = now

        buy_candidate = cfg.mode == "trade" and s.side == "LONG"
        execute = (
            buy_candidate
            and s.symbol not in st.positions
            and len(st.positions) < cfg.max_positions
        )
        note = ""
        if buy_candidate:
            note = ("🤖 Ejecutando compra…" if execute
                    else "🤖 No ejecutada (ya abierta o límite de posiciones)")

        if detailed < cfg.max_alerts:
            await tg.send(signal_message(s, note))
            detailed += 1
        else:
            extra.append(s)

        if execute:
            try:
                await open_long(ex, cfg, st, tg, s)
            except Exception as e:
                log.exception("Error comprando %s", s.symbol)
                await tg.send(f"❌ Error comprando {html.escape(s.symbol)}: {html.escape(str(e))[:200]}")

    if extra:
        await tg.send(summary_message(extra))
    st.save()


async def run_scan(ex, cfg, st, tg, fail_until) -> dict:
    """Un ciclo: 5m para todos los pares; 15m y 1h solo para los que cruzan."""
    stats = {"pairs": 0, "crosses": 0, "signals": 0}

    symbols = await ex.symbols()
    symbols += [s for s in st.positions if s not in symbols]
    if not symbols:
        log.warning("Ningún par cumple los filtros (volumen, quote, blacklist)")
        return stats

    raw5 = await fetch_many(ex, cfg, symbols, "5m")
    d5s, cands = {}, []
    for sym, raw in raw5.items():
        if len(raw) < MIN_BARS:
            continue
        d5 = prep_5m(raw)
        d5s[sym] = d5
        side = trigger(d5)
        if side:
            cands.append((sym, side))
    stats["pairs"], stats["crosses"] = len(d5s), len(cands)
    log.info("Pares analizados: %d · cruces 5m: %d", len(d5s), len(cands))

    if cfg.mode == "trade":
        await process_exits(ex, st, tg, d5s, fail_until)

    sem = asyncio.Semaphore(cfg.concurrency)

    async def confirm(sym, side):
        async with sem:
            try:
                r15, r1h = await asyncio.gather(
                    retry(ex.ohlcv, sym, "15m", CANDLES),
                    retry(ex.ohlcv, sym, "1h", CANDLES),
                )
                return build_signal(
                    sym, d5s[sym],
                    prep_htf(r15, "15m", (200,)),
                    prep_htf(r1h, "1h", (72, 89, 200)),
                    side, cfg,
                )
            except Exception as e:
                log.warning("%s confirmación: %s", sym, e)
                return None

    results = await asyncio.gather(*(confirm(s, side) for s, side in cands))
    signals = sorted((r for r in results if r), key=lambda x: x.score, reverse=True)
    stats["signals"] = len(signals)
    if signals:
        await dispatch(ex, cfg, st, tg, signals)
    return stats


# ---------------- bucle principal ----------------

async def wait_next_candle(ex, cfg, st, tg, fail_until):
    """Espera al siguiente cierre de vela. Mientras tanto vigila precios si hay posiciones."""
    end = time.time() + seconds_to_next_candle()
    while time.time() < end:
        if cfg.mode == "trade" and st.positions:
            try:
                await live_exits(ex, st, tg, fail_until)
            except Exception:
                log.exception("Error en la vigilancia de posiciones")
        left = end - time.time()
        if left > 0:
            await asyncio.sleep(min(cfg.monitor_seconds, left))


async def main():
    cfg = load_config()
    st = State(cfg.state_file)
    tg = Telegram(cfg.tg_token, cfg.tg_chat_id)
    ex = BingXClient(cfg)

    while True:
        try:
            await ex.load()
            break
        except Exception:
            log.exception("No se pudieron cargar los mercados de BingX; reintento en 30 s")
            await asyncio.sleep(30)

    extra = ""
    if cfg.mode == "trade":
        extra = (f" · {fmt(cfg.trade_usdt)} {cfg.quote} por operación"
                 f" · máx. {cfg.max_positions} posiciones")
        if not os.getenv("RAILWAY_VOLUME_MOUNT_PATH"):
            extra += ("\n⚠️ No hay volumen persistente: si Railway reinicia el servicio, "
                      "el bot olvida las posiciones abiertas. Añade un Volume (ver README).")
    await tg.send(
        f"🚀 <b>Bot iniciado</b> · modo <b>{cfg.mode}</b> · TF 5m · {cfg.quote}\n"
        f"Score mínimo {cfg.min_score}/7 · cooldown {cfg.cooldown_min} min{extra}"
    )

    fail_until: dict = {}
    last_hb = 0.0
    try:
        while True:
            stats, ok = {}, False
            try:
                stats = await run_scan(ex, cfg, st, tg, fail_until)
                ok = True
            except Exception as e:
                log.exception("Error en ciclo")
                await tg.send(f"⚠️ Error en ciclo: {html.escape(str(e))[:300]}")
            now = time.time()
            if ok and cfg.heartbeat_min > 0 and now - last_hb >= cfg.heartbeat_min * 60:
                await tg.send(heartbeat_message(stats, len(st.positions), cfg.mode))
                last_hb = now
            await wait_next_candle(ex, cfg, st, tg, fail_until)
    finally:
        await ex.close()


async def supervisor():
    """Si main() falla por cualquier motivo, lo registra y reinicia sin que caiga el servicio."""
    while True:
        try:
            await main()
        except Exception as e:
            log.exception("ERROR FATAL en main(): %s", e)
            try:
                cfg = load_config()
                await Telegram(cfg.tg_token, cfg.tg_chat_id).send(
                    f"💥 Error fatal: {html.escape(type(e).__name__)}: "
                    f"{html.escape(str(e))[:300]}\nReinicio en 30 s."
                )
            except Exception:
                pass
            await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(supervisor())
