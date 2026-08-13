from datetime import datetime, timedelta, timezone

import pytest

from shanzhai.domain import Candle, closed_candles, latest_breakout, newest_volume_spike, pivot_highs

UTC = timezone.utc


def candle(open_time: datetime, high: float, close: float, volume: float = 1.0, quote: float = 1.0) -> Candle:
    return Candle(
        open_time=open_time,
        close_time=open_time + timedelta(hours=4),
        open=close - 1,
        high=high,
        low=min(close, high) - 1,
        close=close,
        volume=volume,
        quote_volume=quote,
    )


def daily_candles(closes: list[float], volumes: list[float]) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            open_time=start + timedelta(days=i),
            close_time=start + timedelta(days=i + 1),
            open=close - 1,
            high=close + 1,
            low=close - 2,
            close=close,
            volume=volumes[i],
            quote_volume=volumes[i] * close * 100,
        )
        for i, (close, volume) in enumerate(zip(closes, volumes))
    ]


class TestCandle:
    def test_closed_at_excludes_open_candle(self):
        now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
        c = candle(datetime(2026, 1, 2, 8, 0, tzinfo=UTC), 10, 10)
        assert c.closed_at(now) is False

    def test_closed_at_includes_finished_candle(self):
        now = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
        c = candle(datetime(2026, 1, 2, 4, 0, tzinfo=UTC), 10, 10)
        assert c.closed_at(now) is True

    def test_closed_candles_filters(self):
        now = datetime(2026, 1, 2, 11, 0, tzinfo=UTC)
        candles = [
            candle(datetime(2026, 1, 2, 4, 0, tzinfo=UTC), 10, 10),
            candle(datetime(2026, 1, 2, 8, 0, tzinfo=UTC), 10, 10),
            candle(datetime(2026, 1, 2, 12, 0, tzinfo=UTC), 10, 10),
        ]
        assert len(closed_candles(candles, now)) == 1


class TestPivotHighs:
    def test_basic_pivot(self):
        candles = [candle(datetime(2026, 1, 1, 0, 0, tzinfo=UTC) + timedelta(hours=4 * i), high=100.0, close=100.0) for i in range(10)]
        candles[5] = candle(datetime(2026, 1, 1, 20, 0, tzinfo=UTC), high=120.0, close=100.0)
        pivots = pivot_highs(candles)
        assert len(pivots) == 1
        assert pivots[0].price == 120.0
        assert pivots[0].index == 5

    def test_is_non_repainting(self):
        candles = [candle(datetime(2026, 1, 1, 0, 0, tzinfo=UTC) + timedelta(hours=4 * i), high=100.0, close=100.0) for i in range(12)]
        candles[5] = candle(datetime(2026, 1, 1, 20, 0, tzinfo=UTC), high=120.0, close=100.0)
        assert pivot_highs(candles[:8]) == []  # right window not yet closed
        assert len(pivot_highs(candles[:9])) == 1  # confirmed after 3 later candles

    def test_rejects_non_positive_windows(self):
        candles = [candle(datetime(2026, 1, 1, 0, 0, tzinfo=UTC) + timedelta(hours=4), high=100.0, close=100.0)]
        with pytest.raises(ValueError):
            pivot_highs(candles, left=0)


class TestLatestBreakout:
    def _series(self) -> list[Candle]:
        candles = [candle(datetime(2026, 1, 1, 0, 0, tzinfo=UTC) + timedelta(hours=4 * i), high=105.0, close=102.0) for i in range(12)]
        candles[6] = candle(datetime(2026, 1, 2, 0, 0, tzinfo=UTC), high=120.0, close=115.0)  # pivot
        candles[10] = candle(datetime(2026, 1, 2, 16, 0, tzinfo=UTC), high=121.0, close=119.0)  # prev
        candles[11] = candle(datetime(2026, 1, 2, 20, 0, tzinfo=UTC), high=125.0, close=125.0)  # breakout close
        return candles

    def test_signal_when_close_crosses_pivot(self):
        signal = latest_breakout(self._series())
        assert signal is not None
        assert signal["pivot_price"] == 120.0
        assert signal["close"] == 125.0
        assert signal["previous_close"] == 119.0
        assert signal["breakout_pct"] == pytest.approx(125.0 / 120.0 * 100 - 100)
        assert signal["dedupe_suffix"] == "4h:" + str(int(datetime(2026, 1, 2, 0, 0, tzinfo=UTC).timestamp()))

    def test_no_signal_when_close_stays_below(self):
        candles = self._series()
        candles[11] = candle(datetime(2026, 1, 2, 20, 0, tzinfo=UTC), high=119.0, close=118.0)
        assert latest_breakout(candles) is None

    def test_pivot_must_be_confirmed_before_signal_candle(self):
        candles = [candle(datetime(2026, 1, 1, 0, 0, tzinfo=UTC) + timedelta(hours=4 * i), high=105.0, close=102.0) for i in range(9)]
        candles[5] = candle(datetime(2026, 1, 1, 20, 0, tzinfo=UTC), high=120.0, close=115.0)
        candles[8] = candle(datetime(2026, 1, 2, 8, 0, tzinfo=UTC), high=125.0, close=125.0)
        assert latest_breakout(candles) is None  # pivot index 5 + 3 == 8 is not < 9

    def test_short_series_returns_none(self):
        candles = [candle(datetime(2026, 1, 1, 0, 0, tzinfo=UTC) + timedelta(hours=4 * i), high=105.0, close=102.0) for i in range(6)]
        assert latest_breakout(candles) is None


class TestNewestVolumeSpike:
    def test_newest_qualifying_wins(self):
        volumes = [1.0] * 7 + [6.0, 1.0, 1.0] + [1.0] * 7 + [9.0, 1.0]
        candles = daily_candles([100.0] * len(volumes), volumes)
        spike = newest_volume_spike(candles)
        assert spike is not None
        assert spike["ratio"] == pytest.approx(9.0)
        assert spike["date"] == candles[-2].open_time.date().isoformat()
        assert spike["watch_until"] == (candles[-2].open_time + timedelta(days=10)).date().isoformat()

    def test_threshold_boundary(self):
        volumes = [1.0] * 7 + [5.0] + [1.0] * 9
        candles = daily_candles([100.0] * len(volumes), volumes)
        spike = newest_volume_spike(candles)
        assert spike is not None
        assert spike["ratio"] == pytest.approx(5.0)

    def test_no_spike_below_threshold(self):
        volumes = [1.0] * 7 + [4.9] + [1.0] * 9
        candles = daily_candles([100.0] * len(volumes), volumes)
        assert newest_volume_spike(candles) is None

    def test_insufficient_history(self):
        candles = daily_candles([100.0] * 7, [1.0] * 7)
        assert newest_volume_spike(candles) is None

    def test_ignores_candles_older_than_check_window(self):
        volumes = [30.0] * 7 + [1.0] * 10  # spike outside last 10 check days
        candles = daily_candles([100.0] * len(volumes), volumes)
        assert newest_volume_spike(candles) is None

    def test_quote_volume_reported(self):
        volumes = [1.0] * 7 + [6.0] + [1.0] * 9
        candles = daily_candles([100.0] * len(volumes), volumes)
        spike = newest_volume_spike(candles)
        assert spike is not None
        assert spike["quote_volume"] == pytest.approx(6.0 * 100 * 100)
