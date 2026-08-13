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
        """Deterministic internal-layer bullish BOS ending on the last candle.

        index 2 deep low -> leg 0->1; index 9 high 110 -> leg 1->0 (swing high
        level); last candle close 112 crosses 110 with prev close 100 -> BOS.
        """
        pattern = symbol in self.breakouts
        boundary = self.now.replace(minute=0, second=0, microsecond=0)
        boundary = boundary - timedelta(hours=boundary.hour % 4)
        out = []
        for i in range(40, 0, -1):
            close_t = boundary - timedelta(hours=i - 1)
            open_t = close_t - timedelta(hours=4)
            open_, high, low, close = 101.0, 105.0, 99.0, 102.0
            if pattern:
                if i == 1:  # index 39 — crossing close
                    open_, high, low, close = 108.0, 115.0, 106.0, 112.0
                elif i == 2:  # index 38 — previous close
                    open_, high, low, close = 100.0, 104.0, 98.0, 100.0
                elif i == 31:  # index 9 — swing high candidate
                    open_, high, low, close = 104.0, 110.0, 102.0, 105.0
                elif i == 34:  # index 6
                    open_, high, low, close = 100.0, 104.0, 98.0, 101.0
                elif i == 36:  # index 4
                    open_, high, low, close = 100.0, 104.0, 97.0, 101.0
                elif i == 38:  # index 2 — deep low
                    open_, high, low, close = 95.0, 100.0, 80.0, 96.0
            out.append(Candle(open_t, close_t, open_, high, low, close, 100.0, 10000.0))
        return out
