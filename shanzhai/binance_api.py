from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .domain import Candle


class BinancePublicClient:
    def __init__(self, base_url: str = "https://api.binance.com", timeout: float = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None):
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "shanzhai-signal-desk/0.1"})
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    return json.load(response)
            except Exception as exc:  # network boundary; re-raised after bounded retry
                last_error = exc
                if attempt < 4:
                    time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"Binance request failed: {path}") from last_error

    def usdt_symbols(self) -> list[str]:
        info = self._get("/api/v3/exchangeInfo")
        return sorted(
            item["symbol"]
            for item in info["symbols"]
            if item["status"] == "TRADING" and item["quoteAsset"] == "USDT" and item.get("isSpotTradingAllowed", True)
        )

    def candles(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        rows = self._get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        return [
            Candle(
                open_time=datetime.fromtimestamp(row[0] / 1000, timezone.utc),
                close_time=datetime.fromtimestamp(row[6] / 1000, timezone.utc),
                open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]),
                volume=float(row[5]), quote_volume=float(row[7]),
            )
            for row in rows
        ]

