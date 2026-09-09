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
# 1h structure confirmation window (left/right candles each side), used by the
# HT-structure/LT-trigger scan (scan_ht_lt) that feeds the pipeline.
HT_STRUCTURE_SIZE = 5

BOS = "BOS"
CHOCH = "CHoCH"

BULLISH_LEG = 1
BEARISH_LEG = 0

# Quality gate applied before a signal becomes email-eligible. It does not
# change which crossings are detected (the LuxAlgo port above stays faithful);
# it only decides which events pass a "real structural change" bar: a minimum
# breakout margin, a body fully beyond the level, and an expansion in volume.
QUALITY_MIN_BREAKOUT_PCT = 1.0
QUALITY_BODY_CONFIRM = True
QUALITY_VOLUME_MULT = 1.5
QUALITY_VOLUME_LOOKBACK = 20


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
    candle_open: float = 0.0
    candle_volume: float = 0.0
    avg_volume: float = 0.0
    level_tag: str = "first"  # "first" = 首次突破 (website only), "second" = 二次突破 (email)

    @property
    def key(self) -> str:
        return f"{self.layer}:{self.tag}:{int(self.structure_time.timestamp())}"


def quality_ok(
    event: SmcEvent,
    min_breakout_pct: float = QUALITY_MIN_BREAKOUT_PCT,
    body_confirm: bool = QUALITY_BODY_CONFIRM,
    volume_mult: float = QUALITY_VOLUME_MULT,
) -> bool:
    """True when the crossing looks like a genuine structural move.

    - the close breaks the level by at least ``min_breakout_pct`` percent;
    - ``body_confirm``: the majority of the candle body sits beyond the level
      (``close - level > level - open`` for bullish, reversed for bearish), so
      the break is carried by the body rather than a wick poke;
    - the breakout candle's volume exceeds ``volume_mult`` times the average
      volume of the ``QUALITY_VOLUME_LOOKBACK`` candles before it.
    """
    if event.breakout_pct < min_breakout_pct:
        return False
    if body_confirm:
        if event.direction == "bullish":
            if not (event.close - event.level > event.level - event.candle_open):
                return False
        elif not (event.level - event.close > event.candle_open - event.level):
            return False
    if volume_mult > 0:
        if event.avg_volume <= 0 or event.candle_volume < volume_mult * event.avg_volume:
            return False
    return True


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

        volume_window = candles[max(0, index - QUALITY_VOLUME_LOOKBACK) : index]
        avg_volume = sum(c.volume for c in volume_window) / len(volume_window) if volume_window else 0.0

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
                        candle_open=bar.open, candle_volume=bar.volume, avg_volume=avg_volume,
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
                        candle_open=bar.open, candle_volume=bar.volume, avg_volume=avg_volume,
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


def _structure_timeline(ht_candles: Sequence[Candle], size: int) -> list[tuple]:
    """Find 4h pivot highs/lows (non-repainting, right-confirmed by ``size``
    candles each side) as the structure levels.

    Returns the ordered list of structure updates as
    ``(confirm_time, is_high, level, bar_time)`` tuples: ``confirm_time`` is
    the HT candle close time at which the pivot was right-confirmed and the
    level became actionable; ``bar_time`` is the pivot bar's open time (used as
    the stable structure identity for idempotency).
    """
    timeline: list[tuple] = []
    for index in range(size, len(ht_candles) - size):
        candidate = ht_candles[index]
        left_highs = [c.high for c in ht_candles[index - size : index]]
        right_highs = [c.high for c in ht_candles[index + 1 : index + size + 1]]
        left_lows = [c.low for c in ht_candles[index - size : index]]
        right_lows = [c.low for c in ht_candles[index + 1 : index + size + 1]]
        confirm_time = ht_candles[index + size].close_time
        if candidate.high > max(left_highs) and candidate.high >= max(right_highs):
            timeline.append((confirm_time, True, candidate.high, candidate.open_time))
        if candidate.low < min(left_lows) and candidate.low <= min(right_lows):
            timeline.append((confirm_time, False, candidate.low, candidate.open_time))
    timeline.sort(key=lambda item: item[0])
    return timeline


def scan_ht_lt(
    ht_candles: Sequence[Candle],
    lt_candles: Sequence[Candle],
    size: int = HT_STRUCTURE_SIZE,
    layer: str = "swing",
) -> list[SmcEvent]:
    """Higher-timeframe structure, lower-timeframe trigger.

    Structure levels are confirmed on ``ht_candles`` as right-confirmed pivot
    highs/lows (non-repainting). A pivot only upgrades its side when it exceeds
    the currently active level (higher high for the bull side, lower low for the
    bear side), so later, weaker pivots never re-anchor a stronger structure.
    A BOS/CHoCH fires when a ``lt_candles`` close crosses the currently active
    level. ``signal_time`` is the LT candle's close time and ``structure_time``
    the HT pivot bar's open time, so the idempotency key is anchored to the
    higher-timeframe structure. Trend bias is updated on every fire, exactly as
    in the single-timeframe port.
    """
    events: list[SmcEvent] = []
    timeline = _structure_timeline(ht_candles, size)
    if not timeline:
        return events

    high_level: float | None = None
    high_bar: datetime | None = None
    high_crossed = True
    low_level: float | None = None
    low_bar: datetime | None = None
    low_crossed = True
    bias = 0  # 0 neutral, +1 bullish, -1 bearish
    ti = 0

    for index in range(1, len(lt_candles)):
        bar = lt_candles[index]
        prev_close = lt_candles[index - 1].close
        while ti < len(timeline) and timeline[ti][0] <= bar.close_time:
            _, is_high, level, bar_time = timeline[ti]
            if is_high:
                if high_level is None or level > high_level:
                    high_level, high_bar, high_crossed = level, bar_time, False
            else:
                if low_level is None or level < low_level:
                    low_level, low_bar, low_crossed = level, bar_time, False
            ti += 1

        if (
            high_level is not None
            and high_bar is not None
            and not high_crossed
            and prev_close <= high_level < bar.close
        ):
            tag = CHOCH if bias == -1 else BOS
            bias_before = bias
            high_crossed = True
            bias = 1
            events.append(
                SmcEvent(
                    layer=layer, tag=tag, direction="bullish",
                    level=high_level, close=bar.close, previous_close=prev_close,
                    signal_time=bar.close_time, structure_time=high_bar,
                    bias_before=bias_before,
                    breakout_pct=(bar.close / high_level - 1) * 100,
                    candle_open=bar.open, candle_volume=bar.volume, avg_volume=0.0,
                )
            )

        if (
            low_level is not None
            and low_bar is not None
            and not low_crossed
            and prev_close >= low_level > bar.close
        ):
            tag = CHOCH if bias == 1 else BOS
            bias_before = bias
            low_crossed = True
            bias = -1
            events.append(
                SmcEvent(
                    layer=layer, tag=tag, direction="bearish",
                    level=low_level, close=bar.close, previous_close=prev_close,
                    signal_time=bar.close_time, structure_time=low_bar,
                    bias_before=bias_before,
                    breakout_pct=(low_level / bar.close - 1) * 100,
                    candle_open=bar.open, candle_volume=bar.volume, avg_volume=0.0,
                )
            )
    events.sort(key=lambda event: event.signal_time)
    return events
