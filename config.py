"""Lectura de variables de entorno. En Railway se usan las del servicio; en local, el archivo .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Activos que no se analizan (stablecoins y divisas fiat)
STABLES = {
    "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDD", "USDE", "PYUSD",
    "EUR", "EURT", "GBP", "BRL", "TRY",
}


def _get(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _float(name: str, default: float) -> float:
    return float(_get(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(float(_get(name, str(default))))


def _bool(name: str, default: bool) -> bool:
    return _get(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    bingx_key: str
    bingx_secret: str
    tg_token: str
    tg_chat_id: str
    mode: str
    quote: str
    trade_usdt: float
    max_positions: int
    min_quote_volume: float
    max_symbols: int
    concurrency: int
    monitor_seconds: int
    cooldown_min: int
    min_score: int
    stop_atr_mult: float
    tp_r_mult: float
    max_alerts: int
    heartbeat_min: int
    notify_shorts: bool
    blacklist: frozenset
    state_file: str


def load_config() -> Config:
    mode = _get("MODE", "signals").lower()
    if mode not in ("signals", "trade"):
        raise ValueError("MODE debe ser 'signals' o 'trade'")

    default_state = os.path.join(os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or ".", "state.json")

    cfg = Config(
        bingx_key=_get("BINGX_API_KEY", ""),
        bingx_secret=_get("BINGX_API_SECRET", ""),
        tg_token=_get("TELEGRAM_BOT_TOKEN", ""),
        tg_chat_id=_get("TELEGRAM_CHAT_ID", ""),
        mode=mode,
        quote=_get("QUOTE", "USDT").upper(),
        trade_usdt=_float("TRADE_USDT", 20),
        max_positions=_int("MAX_POSITIONS", 3),
        min_quote_volume=_float("MIN_QUOTE_VOLUME", 500000),
        max_symbols=_int("MAX_SYMBOLS", 0),
        concurrency=_int("CONCURRENCY", 5),
        monitor_seconds=_int("MONITOR_SECONDS", 30),
        cooldown_min=_int("COOLDOWN_MIN", 60),
        min_score=_int("MIN_SCORE", 5),
        stop_atr_mult=_float("STOP_ATR_MULT", 1.5),
        tp_r_mult=_float("TP_R_MULT", 2.0),
        max_alerts=_int("MAX_ALERTS", 10),
        heartbeat_min=_int("HEARTBEAT_MIN", 60),
        notify_shorts=_bool("NOTIFY_SHORTS", True),
        blacklist=frozenset(
            x.strip().upper() for x in _get("BLACKLIST", "").split(",") if x.strip()
        ),
        state_file=_get("STATE_FILE", default_state),
    )

    if cfg.mode == "trade":
        if not (cfg.bingx_key and cfg.bingx_secret):
            raise ValueError("MODE=trade necesita BINGX_API_KEY y BINGX_API_SECRET")
        if cfg.trade_usdt <= 0 or cfg.max_positions <= 0:
            raise ValueError("TRADE_USDT y MAX_POSITIONS deben ser mayores que 0")
    if not 1 <= cfg.min_score <= 7:
        raise ValueError("MIN_SCORE debe estar entre 1 y 7")
    return cfg
