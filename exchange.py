"""Conexión con BingX (spot) mediante ccxt."""
from __future__ import annotations

import logging

import ccxt.async_support as ccxt
import pandas as pd

from config import STABLES

log = logging.getLogger("exchange")
COLS = ["ts", "open", "high", "low", "close", "volume"]


class BingXClient:
    def __init__(self, cfg):
        self.cfg = cfg
        params = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        if cfg.bingx_key:
            params["apiKey"] = cfg.bingx_key
            params["secret"] = cfg.bingx_secret
        self.ex = ccxt.bingx(params)

    async def load(self):
        await self.ex.load_markets()
        log.info("Mercados cargados: %d", len(self.ex.markets))

    async def close(self):
        await self.ex.close()

    async def symbols(self) -> list[str]:
        """Pares spot activos contra QUOTE con volumen suficiente, de mayor a menor volumen."""
        c = self.cfg
        tickers = await self.ex.fetch_tickers()
        ranked = []
        for sym, m in self.ex.markets.items():
            if not m.get("spot") or m.get("quote") != c.quote or m.get("active") is False:
                continue
            base = m.get("base") or ""
            if base in STABLES or base in c.blacklist:
                continue
            t = tickers.get(sym) or {}
            vol = float(t.get("quoteVolume") or 0)
            if vol <= 0:
                vol = float(t.get("baseVolume") or 0) * float(t.get("last") or 0)
            if vol >= c.min_quote_volume:
                ranked.append((vol, sym))
        ranked.sort(reverse=True)
        syms = [s for _, s in ranked]
        return syms[: c.max_symbols] if c.max_symbols > 0 else syms

    async def ohlcv(self, sym: str, tf: str, limit: int = 500) -> pd.DataFrame:
        raw = await self.ex.fetch_ohlcv(sym, tf, limit=limit)
        return pd.DataFrame(raw, columns=COLS)

    async def price(self, sym: str) -> float:
        t = await self.ex.fetch_ticker(sym)
        return float(t.get("last") or 0)

    async def free(self, asset: str) -> float:
        bal = await self.ex.fetch_balance()
        return float((bal.get("free") or {}).get(asset) or 0)

    async def buy_quote(self, sym: str, usdt: float) -> dict:
        """Compra a mercado por un importe en USDT."""
        order = await self.ex.create_order(sym, "market", "buy", None, None, {"cost": usdt})
        if order.get("id") and not order.get("filled"):
            try:
                order = await self.ex.fetch_order(order["id"], sym)
            except Exception as e:
                log.warning("No se pudo leer la compra de %s: %s", sym, e)
        return order

    async def sell(self, sym: str, max_qty: float):
        """Vende hasta max_qty sin superar el saldo libre.
        Devuelve (orden, cantidad) o None si no hay saldo que vender."""
        base = sym.split("/")[0]
        qty = min(float(max_qty), await self.free(base))
        if qty <= 0:
            return None
        qty = float(self.ex.amount_to_precision(sym, qty))
        if qty <= 0:
            return None
        order = await self.ex.create_order(sym, "market", "sell", qty)
        if order.get("id") and not order.get("filled"):
            try:
                order = await self.ex.fetch_order(order["id"], sym)
            except Exception as e:
                log.warning("No se pudo leer la venta de %s: %s", sym, e)
        return order, qty
