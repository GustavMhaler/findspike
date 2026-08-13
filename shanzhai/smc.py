"""LuxAlgo Smart Money Concepts port — swing/internal structure BOS & CHoCH.

Faithful port of "Smart Money Concepts [LuxAlgo]" (Pine v5, CC BY-NC-SA 4.0)
signal layer:

- A structure bar is right-confirmed by `size` later candles, so it can never
  repaint: at bar i the candidate is candle i-size, and it qualifies when
  `high[i-size] > max(high[i-size+1 .. i])` (swing high) or
  `low[i-size] < min(low[i-size+1 .. i])` (swing low).
- Following the Pine `var leg` state machine, a structure level is updated only
  on a leg FLIP (leg changes 0 -> 1 or 1 -> 0). Two consecutive swing highs do
  not re-anchor the level; the level stands until the opposite swing appears.
- A signal fires at most once per structure level, when close crosses the level
  (`ta.crossover` / `ta.crossunder` semantics, so the previous close must be on
  the other side). The tag is BOS when the run trend bias is in the same
  direction and CHoCH when it reverses; the bias is updated on every fire.
- Two layers are evaluated: swing (size 50, as `getCurrentStructure(swingsLengthInput)`)
  and internal (size 5, as `getCurrentStructure(5, false, true)`). The internal
  layer additionally requires its level to differ from the swing level and the
  bar shape to match (upper wick larger than lower wick for bullish, reversed
  for bearish — the source's `bullishBar`/`bearishBar` intended logic).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from .domain import Candle

SWING_SIZE = 50
INTERNAL_SIZE = 5

BOS = "BOS"
CHOCH = "CHoCH"

BULLISH_LEG = 1
BEARISH_LEG = 0


@dataclass
class Structure:
    current_level: float | None = None
    last_level: float | None = None
    crossed: bool = False
    bar_time: datetime | None = None


@dataclass
class SmcEvent:
    layer: str
    tag: str
    direction: str
    level: float
    close: float
    previous_close: float
    signal_time: datetime
    structure_time: datetime
    bias_before: int
    breakout_pct: float

    @property
    def key(self) -> str:
        return f"{self.layer}:{self.tag}:{int(self.structure_time.timestamp())}"


def _leg_at(candles: Sequence[Candle], index: int, size: int, prev_leg: int) -> int:
    """Pine `leg(size)` with `var` persistence, evaluated at bar `index`."""
    candidate = candles[index - size]
    window_highs = [c.high for c in candles[index - size + 1 : index + 1]]
    window_lows = [c.low for c in candles[index - size + 1 : index + 1]]
    if candidate.high > max(window_highs):
        return BEARISH_LEG
    if candidate.low < min(window_lows):
        return BULLISH_LEG
    return prev_leg


def _bullish_bar(candle: Candle) -> bool:
    return candle.high - max(candle.open, candle.close) > min(candle.open, candle.close) - candle.low


def _bearish_bar(candle: Candle) -> bool:
    return candle.high - max(candle.open, candle.close) < min(candle.open, candle.close) - candle.low


def _run_layer(
    candles: Sequence[Candle],
    size: int,
    layer: str,
    swing_high: Structure | None = None,
    swing_low: Structure | None = None,
) -> tuple[list[SmcEvent], Structure, Structure, int]:
    """Evaluate one structure layer over closed candles (Pine per-bar order)."""
    events: list[SmcEvent] = []
    high = Structure()
    low = Structure()
    bias = 0  # 0 neutral, +1 bullish, -1 bearish
    leg = BEARISH_LEG  # Pine: var leg = 0
    for index in range(size, len(candles)):
        new_leg = _leg_at(candles, index, size, leg)
        if new_leg != leg:
            candidate = candles[index - size]
            if new_leg == BULLISH_LEG:  # startOfBullishLeg -> swing low update
                low.last_level = low.current_level
                low.current_level = candidate.low
                low.crossed = False
                low.bar_time = candidate.open_time
            else:  # startOfBearishLeg -> swing high update
                high.last_level = high.current_level
                high.current_level = candidate.high
                high.crossed = False
                high.bar_time = candidate.open_time
            leg = new_leg

        bar = candles[index]
        prev_close = candles[index - 1].close

        high_level = high.current_level
        if (
            high_level is not None
            and high.bar_time is not None
            and prev_close <= high_level < bar.close
            and not high.crossed
        ):
            if layer == "swing" or (
                swing_high is not None
                and high_level != swing_high.current_level
                and _bullish_bar(bar)
            ):
                tag = CHOCH if bias == -1 else BOS
                bias_before = bias
                high.crossed = True
                bias = 1
                events.append(
                    SmcEvent(
                        layer=layer, tag=tag, direction="bullish",
                        level=high_level, close=bar.close, previous_close=prev_close,
                        signal_time=bar.close_time, structure_time=high.bar_time,
                        bias_before=bias_before,
                        breakout_pct=(bar.close / high_level - 1) * 100,
                    )
                )

        low_level = low.current_level
        if (
            low_level is not None
            and low.bar_time is not None
            and prev_close >= low_level > bar.close
            and not low.crossed
        ):
            if layer == "swing" or (
                swing_low is not None
                and low_level != swing_low.current_level
                and _bearish_bar(bar)
            ):
                tag = CHOCH if bias == 1 else BOS
                bias_before = bias
                low.crossed = True
                bias = -1
                events.append(
                    SmcEvent(
                        layer=layer, tag=tag, direction="bearish",
                        level=low_level, close=bar.close, previous_close=prev_close,
                        signal_time=bar.close_time, structure_time=low.bar_time,
                        bias_before=bias_before,
                        breakout_pct=(low_level / bar.close - 1) * 100,
                    )
                )
    return events, high, low, bias


def scan_smc(
    candles: Sequence[Candle],
    swing_size: int = SWING_SIZE,
    internal_size: int = INTERNAL_SIZE,
) -> list[SmcEvent]:
    """Run the full SMC structure scan; returns events ordered by signal time."""
    if len(candles) <= min(swing_size, internal_size):
        return []
    swing_events, swing_high, swing_low, _ = _run_layer(candles, swing_size, "swing")
    internal_events, _, _, _ = _run_layer(candles, internal_size, "internal", swing_high, swing_low)
    events = swing_events + internal_events
    events.sort(key=lambda event: event.signal_time)
    return events
