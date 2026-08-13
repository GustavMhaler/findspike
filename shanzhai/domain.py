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


@dataclass(frozen=True)
class PivotHigh:
    index: int
    time: datetime
    price: float


def closed_candles(candles: Iterable[Candle], now: datetime) -> list[Candle]:
    return [c for c in candles if c.closed_at(now)]


def pivot_highs(candles: Sequence[Candle], left: int = 3, right: int = 3) -> list[PivotHigh]:
    """Return non-repainting pivot highs confirmed by `right` later candles."""
    if left < 1 or right < 1:
        raise ValueError("pivot windows must be positive")
    pivots: list[PivotHigh] = []
    for i in range(left, len(candles) - right):
        candidate = candles[i].high
        left_highs = (c.high for c in candles[i - left : i])
        right_highs = (c.high for c in candles[i + 1 : i + right + 1])
        if candidate > max(left_highs) and candidate >= max(right_highs):
            pivots.append(PivotHigh(i, candles[i].open_time, candidate))
    return pivots


def latest_breakout(candles: Sequence[Candle], left: int = 3, right: int = 3) -> dict | None:
    """Detect a close crossing the latest pivot known before the current candle."""
    if len(candles) < left + right + 2:
        return None
    current_index = len(candles) - 1
    eligible = [p for p in pivot_highs(candles, left, right) if p.index + right < current_index]
    if not eligible:
        return None
    pivot = eligible[-1]
    previous, current = candles[-2], candles[-1]
    if previous.close <= pivot.price < current.close:
        return {
            "signal_time": current.close_time.isoformat(),
            "candle_open_time": current.open_time.isoformat(),
            "close": current.close,
            "previous_close": previous.close,
            "pivot_price": pivot.price,
            "pivot_time": pivot.time.isoformat(),
            "breakout_pct": (current.close / pivot.price - 1) * 100,
            "dedupe_suffix": f"4h:{int(pivot.time.timestamp())}",
        }
    return None


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

