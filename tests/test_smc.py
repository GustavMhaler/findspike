from datetime import datetime, timedelta, timezone

import pytest

from shanzhai.domain import Candle
from shanzhai.smc import BOS, CHOCH, scan_smc

UTC = timezone.utc


def candle(open_time: datetime, open_p: float, high: float, low: float, close: float) -> Candle:
    return Candle(
        open_time=open_time,
        close_time=open_time + timedelta(hours=4),
        open=open_p, high=high, low=low, close=close,
        volume=100.0, quote_volume=10000.0,
    )


def base_series(n: int, start: datetime, open_p=101.0, high=105.0, low=99.0, close=102.0) -> list[Candle]:
    return [candle(start + timedelta(hours=4 * i), open_p, high, low, close) for i in range(n)]


def series_with_bos():
    """Internal-layer bullish BOS: deep low at 2, swing high at 9, close crosses at 16."""
    candles = base_series(40, datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
    candles[2] = candle(candles[2].open_time, 95.0, 100.0, 80.0, 96.0)   # swing low candidate
    candles[4] = candle(candles[4].open_time, 100.0, 104.0, 97.0, 101.0)
    candles[6] = candle(candles[6].open_time, 100.0, 104.0, 98.0, 101.0)
    candles[9] = candle(candles[9].open_time, 104.0, 110.0, 102.0, 105.0)  # swing high candidate
    candles[15] = candle(candles[15].open_time, 100.0, 104.0, 98.0, 100.0)  # previous close
    candles[16] = candle(candles[16].open_time, 108.0, 115.0, 106.0, 112.0)  # crossing close
    return candles


class TestSmcStructure:
    def test_internal_bullish_bos(self):
        events = scan_smc(series_with_bos())
        assert len(events) == 1
        event = events[0]
        assert event.tag == BOS
        assert event.direction == "bullish"
        assert event.layer == "internal"
        assert event.level == 110.0
        assert event.close == 112.0
        assert event.previous_close == 100.0
        assert event.breakout_pct == pytest.approx((112.0 / 110.0 - 1) * 100, abs=1e-9)

    def test_no_repaint_structure_confirmed_late(self):
        """The level only becomes knowable `size` candles after the swing bar."""
        candles = series_with_bos()
        assert scan_smc(candles[:14]) == []  # swing high at 9 not yet confirmed
        assert len(scan_smc(candles[:15])) == 0  # confirmed at 14, not yet crossed
        assert len(scan_smc(candles[:17])) == 1

    def test_crossed_guard_fires_once_per_level(self):
        candles = series_with_bos()
        candles[17] = candle(candles[17].open_time, 112.0, 118.0, 110.0, 116.0)  # stays above
        events = scan_smc(candles)
        assert len(events) == 1  # level was crossed once; no second fire

    def test_level_updates_only_on_leg_flip(self):
        """A second higher swing high without a low in between does not re-anchor."""
        candles = base_series(40, datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
        candles[2] = candle(candles[2].open_time, 95.0, 100.0, 80.0, 96.0)
        candles[9] = candle(candles[9].open_time, 104.0, 110.0, 102.0, 105.0)
        candles[15] = candle(candles[15].open_time, 100.0, 115.0, 99.0, 100.0)  # higher high, outside window
        candles[16] = candle(candles[16].open_time, 108.0, 115.0, 106.0, 112.0)  # crosses 110
        events = [e for e in scan_smc(candles) if e.direction == "bullish"]
        # level stands at 110 (first flip); the 115 bar never flips the leg
        assert events and events[0].level == 110.0
        assert events[0].close == 112.0
        assert len(events) == 1

    def test_choch_after_bearish_bias(self):
        """A bearish crossunder after a bullish run is CHoCH."""
        candles = base_series(40, datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
        candles[2] = candle(candles[2].open_time, 95.0, 100.0, 80.0, 96.0)
        candles[4] = candle(candles[4].open_time, 100.0, 104.0, 97.0, 101.0)
        candles[6] = candle(candles[6].open_time, 100.0, 104.0, 98.0, 101.0)
        candles[9] = candle(candles[9].open_time, 104.0, 110.0, 102.0, 105.0)
        candles[15] = candle(candles[15].open_time, 100.0, 104.0, 98.0, 100.0)
        candles[16] = candle(candles[16].open_time, 108.0, 115.0, 106.0, 112.0)  # bullish BOS
        candles[22] = candle(candles[22].open_time, 101.0, 103.0, 95.0, 102.0)  # swing low candidate
        candles[27] = candle(candles[27].open_time, 103.0, 105.0, 101.0, 102.0)  # prev close
        candles[28] = candle(candles[28].open_time, 100.0, 102.0, 88.0, 92.0)  # bearish bar, crosses 95
        events = scan_smc(candles)
        bullish = [e for e in events if e.direction == "bullish"]
        bearish = [e for e in events if e.direction == "bearish"]
        assert bullish and bullish[0].tag == BOS
        assert bearish and bearish[0].tag == CHOCH
        assert bearish[0].level == 95.0
        assert bearish[0].close == 92.0

    def test_swing_layer_requires_50_candles(self):
        candles = series_with_bos()
        layers = {e.layer for e in scan_smc(candles)}
        assert layers == {"internal"}  # 40 candles is too short for swing (50)

    def test_too_short_series_returns_empty(self):
        assert scan_smc(base_series(4, datetime(2026, 1, 1, 0, 0, tzinfo=UTC))) == []

    def test_flat_series_no_events(self):
        candles = base_series(80, datetime(2026, 1, 1, 0, 0, tzinfo=UTC))
        assert scan_smc(candles) == []
