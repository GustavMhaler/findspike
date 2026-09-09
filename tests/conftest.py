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
        if interval == "4h":
            return self._ht4h(symbol)
        if interval == "15m":
            return self._lt15m(symbol)
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

    def _ht4h(self, symbol: str) -> list[Candle]:
        """Deterministic 4h structure: a pivot high of 110 around the middle of
        the series (right-confirmed by 3 candles each side with pivot size 3),
        so the level 110 is actionable for the 15m trigger series.
        """
        pattern = symbol in self.breakouts
        boundary = self.now.replace(minute=0, second=0, microsecond=0)
        out = []
        for i in range(80, 0, -1):
            close_t = boundary - timedelta(hours=4 * (i - 1))
            open_t = close_t - timedelta(hours=4)
            open_, high, low, close, volume = 101.0, 104.0, 98.0, 102.0, 100.0
            if pattern:
                if i == 40:  # pivot high candidate (110), flanked by lower highs
                    open_, high, low, close = 105.0, 110.0, 100.0, 106.0
                elif i in (37, 38, 39, 41, 42, 43):
                    open_, high, low, close = 101.0, 104.0, 98.0, 102.0
            out.append(Candle(open_t, close_t, open_, high, low, close, volume, volume * 100.0))
        return out

    def _lt15m(self, symbol: str) -> list[Candle]:
        """Deterministic 15m trigger window: closes at 100 until two crossing
        candles — index 799 closes 112 (crossing the active 1h pivot high 110,
        the symbol's FIRST breakout) and index 979 closes 118 (crossing the
        upgraded 1h pivot high 115, the SECOND breakout / 二次突破).
        """
        pattern = symbol in self.breakouts
        boundary = self.now.replace(minute=0, second=0, microsecond=0)
        out = []
        for i in range(1000, 0, -1):
            close_t = boundary - timedelta(minutes=15 * (i - 1))
            open_t = close_t - timedelta(minutes=15)
            open_, high, low, close, volume = 100.0, 104.0, 98.0, 100.0, 100.0
            if pattern:
                if i == 201:  # index 799 — crosses 110 (first breakout)
                    open_, high, low, close, volume = 108.0, 115.0, 106.0, 112.0, 250.0
                elif i == 21:  # index 979 — crosses 115 (second breakout)
                    open_, high, low, close, volume = 114.0, 120.0, 112.0, 118.0, 250.0
            out.append(Candle(open_t, close_t, open_, high, low, close, volume, volume * 100.0))
        return out

    def _hourly(self, symbol: str) -> list[Candle]:
        """Deterministic 1h series used as the structure feed and by the review
        module's 24h measurement.

        Two right-confirmed pivot highs: 110 at index 15 and a higher 115 at
        index 60. The 115 upgrades the active level once it confirms, so the 15m
        trigger series (above) fires a first breakout at 110 and a second
        (二次突破) at 115. The last candle closes 112 so the review module's 24h
        window reads close 112 / +12%.
        """
        pattern = symbol in self.breakouts
        boundary = self.now.replace(minute=0, second=0, microsecond=0)
        out = []
        for i in range(80, 0, -1):
            close_t = boundary - timedelta(hours=i - 1)
            open_t = close_t - timedelta(hours=1)
            open_, high, low, close, volume = 101.0, 105.0, 99.0, 102.0, 100.0
            if pattern:
                if i == 1:  # index 79 — last 1h close (review window)
                    open_, high, low, close, volume = 112.0, 115.0, 108.0, 112.0, 250.0
                elif i == 65:  # index 15 — 1h pivot high (110)
                    open_, high, low, close = 104.0, 110.0, 102.0, 105.0
                elif i == 20:  # index 60 — higher pivot high (115)
                    open_, high, low, close = 109.0, 115.0, 107.0, 110.0
                elif i == 75:  # index 5 — deep low
                    open_, high, low, close = 95.0, 100.0, 80.0, 96.0
            out.append(Candle(open_t, close_t, open_, high, low, close, volume, volume * 100.0))
        return out
