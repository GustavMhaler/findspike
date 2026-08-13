import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from shanzhai.binance_api import BinancePublicClient
from shanzhai.domain import Candle

UTC = timezone.utc


class FakeClient(BinancePublicClient):
    def __init__(self, now: datetime, breakouts: set[str], fail_symbols: set[str] | None = None):
        self.now = now
        self.breakouts = breakouts
        self.fail_symbols = fail_symbols or set()

    def usdt_symbols(self) -> list[str]:
        return ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]

    def candles(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        if symbol in self.fail_symbols:
            raise ConnectionError("boom")
        if interval == "1d":
            return self._daily(symbol)
        return self._four_hour(symbol)

    def _daily(self, symbol: str) -> list[Candle]:
        today = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        for i in range(9, -1, -1):
            open_t = today - timedelta(days=i + 1)
            v = 1.0
            if i == 0:  # newest closed daily candle (yesterday)
                v = {"AAAUSDT": 8.0, "CCCUSDT": 6.0}.get(symbol, 1.0)
            out.append(
                Candle(open_t, open_t + timedelta(days=1), 100.0, 105.0, 95.0, 102.0, v, v * 10000)
            )
        return out

    def _four_hour(self, symbol: str) -> list[Candle]:
        breakout = symbol in self.breakouts
        boundary = self.now.replace(minute=0, second=0, microsecond=0)
        boundary = boundary - timedelta(hours=boundary.hour % 4)
        out = []
        for i in range(40, 0, -1):
            close_t = boundary - timedelta(hours=i - 1)
            open_t = close_t - timedelta(hours=4)
            if breakout and i == 1:
                open_, high, low, close = 111.0, 113.0, 110.0, 112.0
            elif breakout and i == 2:
                open_, high, low, close = 107.0, 109.0, 106.0, 108.0
            elif breakout and i == 5:
                open_, high, low, close = 105.0, 110.0, 104.0, 106.0
            elif breakout and i in (3, 4):
                open_, high, low, close = 104.0, 106.0, 102.0, 104.0
            else:
                open_, high, low, close = 101.0, 105.0, 99.0, 102.0
            out.append(Candle(open_t, close_t, open_, high, low, close, 100.0, 10000.0))
        return out
