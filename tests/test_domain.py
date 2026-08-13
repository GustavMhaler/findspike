from datetime import datetime, timedelta, timezone

import pytest

from shanzhai.domain import Candle, closed_candles, newest_volume_spike

UTC = timezone.utc


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
        c = Candle(
            datetime(2026, 1, 2, 8, 0, tzinfo=UTC),
            datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
            open=10.0, high=11.0, low=9.0, close=10.0, volume=1.0, quote_volume=1.0,
        )
        assert c.closed_at(now) is False

    def test_closed_at_includes_finished_candle(self):
        now = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
        c = Candle(
            datetime(2026, 1, 2, 4, 0, tzinfo=UTC),
            datetime(2026, 1, 2, 8, 0, tzinfo=UTC),
            open=10.0, high=11.0, low=9.0, close=10.0, volume=1.0, quote_volume=1.0,
        )
        assert c.closed_at(now) is True

    def test_closed_candles_filters(self):
        now = datetime(2026, 1, 2, 11, 0, tzinfo=UTC)
        candles = [
            Candle(
                datetime(2026, 1, 2, 4, 0, tzinfo=UTC),
                datetime(2026, 1, 2, 8, 0, tzinfo=UTC),
                open=10.0, high=11.0, low=9.0, close=10.0, volume=1.0, quote_volume=1.0,
            ),
            Candle(
                datetime(2026, 1, 2, 8, 0, tzinfo=UTC),
                datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
                open=10.0, high=11.0, low=9.0, close=10.0, volume=1.0, quote_volume=1.0,
            ),
            Candle(
                datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
                datetime(2026, 1, 2, 16, 0, tzinfo=UTC),
                open=10.0, high=11.0, low=9.0, close=10.0, volume=1.0, quote_volume=1.0,
            ),
        ]
        assert len(closed_candles(candles, now)) == 1


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
