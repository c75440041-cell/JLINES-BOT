"""Mensajes a Telegram (HTML) con reintentos ante límites de velocidad."""
from __future__ import annotations

import asyncio
import html
import logging
import math
from datetime import datetime, timezone

import httpx

log = logging.getLogger("telegram")
MAX_CHARS = 3900


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Parte el texto por saltos de línea para no cortar etiquetas HTML."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > limit and cur:
            parts.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        parts.append(cur)
    return parts


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage" if token else None
        self.chat_id = chat_id

    async def send(self, text: str):
        if not (self.url and self.chat_id):
            log.info("[Telegram desactivado] %s", text.replace("\n", " | ")[:300])
            return
        async with httpx.AsyncClient(timeout=20) as client:
            for part in split_text(text):
                await self._post(client, part)

    async def _post(self, client, text: str):
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        for _ in range(3):
            try:
                r = await client.post(self.url, json=payload)
            except httpx.HTTPError as e:
                log.warning("Telegram (red): %s", e)
                await asyncio.sleep(2)
                continue
            if r.status_code == 200:
                return
            if r.status_code == 429:
                try:
                    wait = int(r.json().get("parameters", {}).get("retry_after", 3))
                except Exception:
                    wait = 3
                await asyncio.sleep(wait + 1)
                continue
            log.warning("Telegram %s: %s", r.status_code, r.text[:200])
            return
        log.warning("Telegram: mensaje descartado tras 3 intentos")


def fmt(x) -> str:
    """Formatea precios con los decimales adecuados para cada magnitud."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "n/d"
    if not math.isfinite(x):
        return "n/d"
    if x == 0:
        return "0"
    digits = min(12, max(2, 6 - int(math.floor(math.log10(abs(x))))))
    return f"{x:.{digits}f}"


def pct(a, b) -> str:
    if not b:
        return "n/d"
    return f"{(a - b) / b * 100:+.2f}%"


def signal_message(s, note: str) -> str:
    emoji = "🟢" if s.side == "LONG" else "🔴"
    risk = abs(s.price - s.stop)
    risk_pct = risk / s.price * 100 if s.price else 0.0
    rr = abs(s.tp - s.price) / risk if risk else 0.0
    v = s.values
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"{emoji} <b>SEÑAL {s.side}</b> · <b>{html.escape(s.symbol)}</b>",
        f"🕒 {now} UTC · TF 5m (vela cerrada)",
        "",
        f"💰 Entrada: <code>{fmt(s.price)}</code>",
        f"🛑 Stop: <code>{fmt(s.stop)}</code> ({pct(s.stop, s.price)})",
        f"🎯 Objetivo: <code>{fmt(s.tp)}</code> ({pct(s.tp, s.price)})",
        f"📐 R:R 1:{rr:.1f} · Riesgo {risk_pct:.2f}%",
        f"📊 Puntuación: {s.score}/{s.max_score}",
        f"📈 Volumen vs media 20: {s.vol_ratio:.2f}×",
        "",
        "<b>Confirmaciones</b>",
    ]
    for text, ok, required in s.checks:
        mark = "✅" if ok else "❌"
        tag = " (obligatoria)" if required else ""
        lines.append(f"{mark} {text}{tag}")

    lines += [
        "",
        "<b>Valores</b>",
        f"EMA9 {fmt(v['e9'])} · VWAP {fmt(v['vwap'])}",
        f"EMA72 / EMA89 (5m): {fmt(v['e72'])} / {fmt(v['e89'])}",
        f"EMA200 5m: {fmt(v['e200'])} · ATR: {fmt(v['atr'])}",
    ]
    if s.side == "SHORT":
        lines.append("\n⚠️ Alerta bajista: en spot no se opera en corto.")
    if note:
        lines.append(f"\n{note}")
    lines.append("\n<i>Señal automática basada en JLines. No es consejo financiero.</i>")
    return "\n".join(lines)


def summary_message(signals) -> str:
    lines = ["📋 <b>Más señales (resumen)</b>"]
    for s in signals:
        icon = "🟢" if s.side == "LONG" else "🔴"
        lines.append(
            f"{icon} {html.escape(s.symbol)} {s.side} · {s.score}/{s.max_score} · "
            f"entrada {fmt(s.price)}"
        )
    return "\n".join(lines)


def heartbeat_message(stats: dict, open_positions: int, mode: str) -> str:
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    return (
        f"💓 <b>Escaneo OK</b> · {now}\n"
        f"Pares: {stats.get('pairs', 0)} · Cruces 5m: {stats.get('crosses', 0)} · "
        f"Señales: {stats.get('signals', 0)}\n"
        f"Modo: {mode} · Posiciones abiertas: {open_positions}"
    )
