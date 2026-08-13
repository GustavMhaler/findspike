from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence


UTC = timezone.utc


@dataclass(frozen=True)
class Candle:
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float

    def closed_at(self, now: datetime) -> bool:
        return self.close_time <= now.astimezone(UTC)


def closed_candles(candles: Iterable[Candle], now: datetime) -> list[Candle]:
    return [c for c in candles if c.closed_at(now)]


def newest_volume_spike(
    candles: Sequence[Candle], days_to_check: int = 10, previous_days: int = 7, threshold: float = 5.0
) -> dict | None:
    """Select the newest qualifying closed daily candle (volume-spike-v2)."""
    if len(candles) < previous_days + 1:
        return None
    start = max(previous_days, len(candles) - days_to_check)
    for i in range(len(candles) - 1, start - 1, -1):
        baseline = candles[i - previous_days : i]
        if len(baseline) != previous_days:
            continue
        average = sum(c.volume for c in baseline) / previous_days
        if average > 0 and candles[i].volume >= threshold * average:
            return {
                "date": candles[i].open_time.date().isoformat(),
                "ratio": candles[i].volume / average,
                "volume": candles[i].volume,
                "quote_volume": candles[i].quote_volume,
                "close": candles[i].close,
                "watch_until": (candles[i].open_time + timedelta(days=10)).date().isoformat(),
            }
    return None

