"""Reglas de entrada y salida, calculadas solo sobre velas cerradas."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from indicators import atr, closed, ema, vwap_daily

MIN_BARS = 210  # historial mínimo para que la EMA200 sea fiable


@dataclass
class Signal:
    symbol: str
    side: str                 # "LONG" o "SHORT"
    price: float              # cierre de la última vela 5m cerrada
    stop: float
    tp: float
    score: int
    max_score: int
    checks: list = field(default_factory=list)   # (texto, ok, obligatoria)
    values: dict = field(default_factory=dict)
    vol_ratio: float = 0.0


def prep_5m(raw: pd.DataFrame) -> pd.DataFrame:
    """Velas 5m cerradas con EMA5, 9, 15, 72, 89 y 200, VWAP diario y ATR."""
    d = closed(raw, "5m")
    if d.empty:
        return d
    for n in (5, 9, 15, 72, 89, 200):
        d[f"e{n}"] = ema(d["close"], n)
    d["vwap"] = vwap_daily(d)
    d["atr"] = atr(d)
    return d


def prep_htf(raw: pd.DataFrame, tf: str, lengths: tuple) -> pd.DataFrame:
    """Velas de marco superior cerradas con las EMA indicadas."""
    d = closed(raw, tf)
    for n in lengths:
        d[f"e{n}"] = ema(d["close"], n)
    return d


def trigger(d5: pd.DataFrame) -> str | None:
    """Cruce de EMA9 con VWAP entre las dos últimas velas cerradas (la flecha del indicador)."""
    if len(d5) < 3:
        return None
    prev, last = d5.iloc[-2], d5.iloc[-1]
    if prev["e9"] <= prev["vwap"] and last["e9"] > last["vwap"]:
        return "LONG"
    if prev["e9"] >= prev["vwap"] and last["e9"] < last["vwap"]:
        return "SHORT"
    return None


def build_signal(symbol: str, d5: pd.DataFrame, d15: pd.DataFrame, d1h: pd.DataFrame,
                 side: str, cfg) -> Signal | None:
    """Aplica las confirmaciones y calcula stop y objetivo. Devuelve None si no pasa el filtro."""
    if min(len(d5), len(d15), len(d1h)) < MIN_BARS:
        return None
    last, q15, h1 = d5.iloc[-1], d15.iloc[-1], d1h.iloc[-1]
    c = float(last["close"])
    long_ = side == "LONG"

    # (texto, condición alcista, condición bajista, obligatoria)
    rules = [
        ("JLines 5m (EMA72 vs EMA89)", last["e72"] > last["e89"], last["e72"] < last["e89"], True),
        ("JLines 1h (EMA72 vs EMA89)", h1["e72"] > h1["e89"], h1["e72"] < h1["e89"], True),
        ("Precio vs EMA200 5m", c > last["e200"], c < last["e200"], False),
        ("Precio vs EMA200 15m", q15["close"] > q15["e200"], q15["close"] < q15["e200"], False),
        ("Precio vs EMA200 1h", h1["close"] > h1["e200"], h1["close"] < h1["e200"], False),
        ("Nube 5/15 EMA (EMA5 vs EMA15)", last["e5"] > last["e15"], last["e5"] < last["e15"], False),
        ("Precio vs VWAP", c > last["vwap"], c < last["vwap"], False),
    ]

    checks, score, required_ok = [], 0, True
    for text, cond_up, cond_dn, required in rules:
        ok = bool(cond_up if long_ else cond_dn)
        checks.append((text, ok, required))
        score += int(ok)
        if required and not ok:
            required_ok = False
    if not required_ok or score < cfg.min_score:
        return None

    atr_v = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
    if atr_v <= 0:
        return None
    risk = cfg.stop_atr_mult * atr_v
    stop = c - risk if long_ else c + risk
    tp = c + cfg.tp_r_mult * risk if long_ else c - cfg.tp_r_mult * risk

    vols = d5["volume"].iloc[-21:-1]
    mean_vol = float(vols.mean()) if len(vols) else 0.0
    vol_ratio = float(last["volume"]) / mean_vol if mean_vol > 0 else 0.0

    return Signal(
        symbol=symbol,
        side="LONG" if long_ else "SHORT",
        price=c,
        stop=stop,
        tp=tp,
        score=score,
        max_score=len(rules),
        checks=checks,
        values={
            "e9": float(last["e9"]),
            "vwap": float(last["vwap"]),
            "e72": float(last["e72"]),
            "e89": float(last["e89"]),
            "e200": float(last["e200"]),
            "atr": atr_v,
        },
        vol_ratio=vol_ratio,
    )


def exit_reason(d5: pd.DataFrame, pos: dict) -> str | None:
    """Motivo de salida de una posición LONG. Solo mira velas cerradas posteriores a la entrada."""
    if len(d5) < 2:
        return None
    prev, last = d5.iloc[-2], d5.iloc[-1]
    if int(last["ts"]) < int(pos.get("since") or 0):
        return None
    if float(last["low"]) <= pos["stop"]:
        return "Stop loss alcanzado (mínimo de vela)"
    if float(last["high"]) >= pos["tp"]:
        return "Take profit alcanzado (máximo de vela)"
    if prev["e9"] >= prev["vwap"] and last["e9"] < last["vwap"]:
        return "EMA9 cruza bajo VWAP"
    return None
