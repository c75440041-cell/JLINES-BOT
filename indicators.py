"""Indicadores calculados como en TradingView: EMA, VWAP diario y ATR de Wilder."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

TF_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def vwap_daily(df: pd.DataFrame) -> pd.Series:
    """VWAP con precio típico hlc3, reiniciado en cada día UTC."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.floor("D")
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum().replace(0, np.nan)
    return pv / vol


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR con suavizado de Wilder (RMA)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def closed(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Devuelve solo las velas ya cerradas; descarta la vela en formación."""
    now_ms = time.time() * 1000
    return df[df["ts"] + TF_MS[tf] <= now_ms].reset_index(drop=True)


def candle_open_ms(tf: str = "5m") -> int:
    """Hora de apertura (ms) de la vela que se está formando ahora."""
    step = TF_MS[tf]
    return int(time.time() * 1000) // step * step
