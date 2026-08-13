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

    def candles(
        self, symbol: str, interval: str, limit: int,
        start_time: datetime | None = None, end_time: datetime | None = None,
    ) -> list[Candle]:
        if symbol in self.fail_symbols:
            raise ConnectionError("boom")
        if interval == "1d":
            return self._daily(symbol)
        return self._hourly(symbol)

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

    def _hourly(self, symbol: str) -> list[Candle]:
        """Deterministic swing-layer bullish BOS ending on the last candle.

        Swing size 50: index 5 deep low -> leg 0->1 (confirmed at i=55);
        index 15 high 110 -> leg 1->0 (confirmed at i=65); last candle close
        112 crosses 110 with prev close 100 -> swing BOS. The internal layer's
        divergence filter suppresses its own signal here (same level).
        """
        pattern = symbol in self.breakouts
        boundary = self.now.replace(minute=0, second=0, microsecond=0)
        out = []
        for i in range(80, 0, -1):
            close_t = boundary - timedelta(hours=i - 1)
            open_t = close_t - timedelta(hours=1)
            open_, high, low, close = 101.0, 105.0, 99.0, 102.0
            if pattern:
                if i == 1:  # index 79 — crossing close
                    open_, high, low, close = 108.0, 115.0, 106.0, 112.0
                elif i == 2:  # index 78 — previous close
                    open_, high, low, close = 100.0, 104.0, 98.0, 100.0
                elif i == 65:  # index 15 — swing high candidate
                    open_, high, low, close = 104.0, 110.0, 102.0, 105.0
                elif i == 60:  # index 20
                    open_, high, low, close = 100.0, 104.0, 98.0, 101.0
                elif i == 55:  # index 25
                    open_, high, low, close = 100.0, 104.0, 97.0, 101.0
                elif i == 75:  # index 5 — deep low
                    open_, high, low, close = 95.0, 100.0, 80.0, 96.0
            out.append(Candle(open_t, close_t, open_, high, low, close, 100.0, 10000.0))
        return out
