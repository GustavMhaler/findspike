from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from .binance_api import BinancePublicClient
from .domain import closed_candles, newest_volume_spike
from .io_utils import read_json, write_json
from .review import compose_reviews
from .smc import scan_ht_lt

ALGORITHM_VERSION = "volume-spike-v2+smc-ht1h-lt15m-first-second-v1"

# Only swing-layer signals are emitted (page, history, email, reviews).
EMITTED_LAYERS = ("swing",)

# Single-layer structure + low-timeframe trigger, following the LuxAlgo
# CHoCH/BOS semantics from docs/choch:
#   structure = 1h pivot levels (right-confirmed, size HT_STRUCTURE_SIZE)
#   lt        = 15m close crossing the active level
# A symbol's FIRST bullish breakout in the recent window only updates the
# website; the SECOND one is emailed and tagged "二次突破".
STRUCTURE_INTERVAL = "1h"
STRUCTURE_LIMIT = 300
LT_INTERVAL = "15m"
LT_LIMIT = 1000

# A bullish breakout is a "二次突破" (second breakout) when the same symbol
# already had a bullish breakout within this window. The first one only updates
# the website; the second one is emailed.
SECOND_BREAKOUT_WINDOW = timedelta(days=3)


def _tag_breakout_order(
    history: list[dict],
    signals: list[dict],
    now: datetime,
    seed: bool = False,
) -> None:
    """Tag each signal as first/second breakout per symbol, in place.

    For every bullish signal, count how many bullish signals the same symbol
    already has within ``SECOND_BREAKOUT_WINDOW`` (prior history plus earlier
    signals of this batch). The first one is ``level_tag="first"`` (website
    only, ``email_ok=False``); the second and later are ``level_tag="second"``
    (二次突破, ``email_ok=True``). Bearish signals never get emailed.
    """
    window_start = now - SECOND_BREAKOUT_WINDOW
    prior: dict[str, int] = {}
    for item in history:
        if item.get("direction") != "bullish":
            continue
        try:
            signal_time = datetime.fromisoformat(item["signal_time"])
        except (KeyError, ValueError):
            continue
        if window_start <= signal_time <= now:
            prior[item["symbol"]] = prior.get(item["symbol"], 0) + 1

    ordered = sorted(signals, key=lambda s: s["signal_time"])
    for signal in ordered:
        if signal.get("direction") != "bullish":
            signal["level_tag"] = "first"
            signal["email_ok"] = False
            signal["notify"] = False
            continue
        prior[signal["symbol"]] = prior.get(signal["symbol"], 0) + 1
        ordinal = prior[signal["symbol"]]
        signal["level_tag"] = "second" if ordinal >= 2 else "first"
        signal["email_ok"] = ordinal >= 2
        signal["notify"] = (
            not seed
            and signal["email_ok"]
            and now - datetime.fromisoformat(signal["signal_time"]) <= timedelta(hours=24)
        )


def _read_json(path: Path, fallback):
    return read_json(path, fallback)


def _write_json(path: Path, value) -> None:
    write_json(path, value)


def scan_daily(client: BinancePublicClient, state_dir: Path, now: datetime, workers: int = 6) -> dict:
    started = time.monotonic()
    symbols = client.usdt_symbols()
    successes: list[dict] = []
    failures: list[dict] = []

    def scan(symbol: str):
        candles = closed_candles(client.candles(symbol, "1d", 24), now)
        spike = newest_volume_spike(candles)
        return {"symbol": symbol, **spike} if spike else None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        jobs = {executor.submit(scan, symbol): symbol for symbol in symbols}
        for job in as_completed(jobs):
            try:
                result = job.result()
                if result:
                    successes.append(result)
            except Exception as exc:
                failures.append({"symbol": jobs[job], "error": type(exc).__name__})

    completed = len(symbols) - len(failures)
    coverage = completed / len(symbols) if symbols else 0
    if coverage < 0.9:
        raise RuntimeError(f"daily scan coverage {coverage:.1%} is below 90%")
    successes.sort(key=lambda item: item["ratio"], reverse=True)
    result = {
        "scanned_at": now.isoformat(), "symbols_total": len(symbols), "symbols_succeeded": completed,
        "symbols_failed": len(failures), "coverage": coverage, "spikes": successes, "failures": failures[:20],
        "duration_seconds": round(time.monotonic() - started, 2),
    }
    _write_json(state_dir / "daily.json", result)
    return result


def scan_choch(client: BinancePublicClient, state_dir: Path, now: datetime, seed: bool = False, workers: int = 6) -> dict:
    started = time.monotonic()
    daily = _read_json(state_dir / "daily.json", {})
    active = [s for s in daily.get("spikes", []) if s.get("watch_until", "") >= now.date().isoformat()]
    sent = set(_read_json(state_dir / "choch_keys.json", []))
    history = _read_json(state_dir / "choch_history.json", [])
    signals: list[dict] = []
    failures: list[dict] = []

    def scan(item: dict):
        structure = closed_candles(client.candles(item["symbol"], STRUCTURE_INTERVAL, STRUCTURE_LIMIT), now)
        lt = closed_candles(client.candles(item["symbol"], LT_INTERVAL, LT_LIMIT), now)
        events = [e for e in scan_ht_lt(structure, lt) if e.layer in EMITTED_LAYERS]
        result = []
        for event in events:
            key = f'{item["symbol"]}:{event.key}'
            result.append(
                {
                    "symbol": item["symbol"], "layer": event.layer, "tag": event.tag,
                    "direction": event.direction, "level": round(event.level, 8),
                    "close": round(event.close, 8), "previous_close": round(event.previous_close, 8),
                    "signal_time": event.signal_time.isoformat(),
                    "structure_time": event.structure_time.isoformat(),
                    "breakout_pct": round(event.breakout_pct, 4),
                    "level_tag": event.level_tag,
                    "trace": [round(c.close, 8) for c in lt[-40:]],
                    "key": key,
                }
            )
        return result, lt

    with ThreadPoolExecutor(max_workers=workers) as executor:
        jobs = {executor.submit(scan, item): item["symbol"] for item in active}
        # (symbol, layer, tag, signal_time) identifies one event; structure_time
        # drifts by a candle or two as candles accumulate, so compare without it
        # to swallow re-derived duplicates. level_tag is derived (first/second
        # breakout) and must not participate in the identity key.
        seen_events = {(h["symbol"], h["layer"], h["tag"], h["signal_time"]) for h in history}
        through = None
        for job in as_completed(jobs):
            try:
                events, candles = job.result()
                if candles:
                    through = max(through, candles[-1].close_time) if through else candles[-1].close_time
                for signal in events:
                    if signal["key"] not in sent:
                        event_sig = (signal["symbol"], signal["layer"], signal["tag"], signal["signal_time"])
                        if event_sig in seen_events:
                            sent.add(signal["key"])
                            continue
                        seen_events.add(event_sig)
                        signals.append(signal)
                        sent.add(signal["key"])
            except Exception as exc:
                failures.append({"symbol": jobs[job], "error": f"{type(exc).__name__}: {str(exc)[:120]}"})

    _tag_breakout_order(history, signals, now, seed=seed)

    coverage = (len(active) - len(failures)) / len(active) if active else 1.0
    if coverage < 0.9:
        raise RuntimeError(f"choch watch-pool coverage {coverage:.1%} is below 90% ({len(failures)}/{len(active)} failed)")

    history = sorted(history + signals, key=lambda item: item["signal_time"], reverse=True)
    cutoff = now - timedelta(days=90)
    history = [item for item in history if datetime.fromisoformat(item["signal_time"]) >= cutoff]
    _write_json(state_dir / "choch_keys.json", sorted(sent))
    _write_json(state_dir / "choch_history.json", history)
    result = {
        "scanned_at": now.isoformat(), "data_candle_through": through.isoformat() if through else None,
        "watch_count": len(active), "signals": signals, "new_signal_count": len(signals),
        "notify_count": sum(bool(s["notify"]) for s in signals), "failures": failures,
        "duration_seconds": round(time.monotonic() - started, 2),
    }
    _write_json(state_dir / "choch.json", result)
    return result


def read_status_sidecar(state_dir: Path) -> dict:
    return _read_json(state_dir / "status.json", {"consecutive_failures": 0})


def write_status_sidecar(state_dir: Path, value: dict) -> None:
    _write_json(state_dir / "status.json", value)


def compose_latest(state_dir: Path, now: datetime) -> dict:
    daily = _read_json(state_dir / "daily.json", {})
    choch = _read_json(state_dir / "choch.json", {})
    history = _read_json(state_dir / "choch_history.json", [])
    cutoff = now - timedelta(days=30)
    history = [
        item for item in history
        if datetime.fromisoformat(item["signal_time"]) >= cutoff and item.get("layer") in EMITTED_LAYERS
    ]
    runtime = {k: daily.get(k) for k in ("symbols_total", "symbols_succeeded", "symbols_failed", "coverage", "duration_seconds")}
    return {
        "schema_version": 2, "algorithm_version": ALGORITHM_VERSION,
        "generated_at": now.isoformat(), "timezone": "Asia/Shanghai", "status": "ok",
        "data_candle_through": choch.get("data_candle_through"),
        "runtime": runtime,
        "volume_spikes": daily.get("spikes", []),
        "choch": {
            "latest_scan_at": choch.get("scanned_at"),
            "new_signal_count": choch.get("new_signal_count", 0),
            "signals": choch.get("signals", []),
            "history": history[:300],
        },
        "reviews": compose_reviews(state_dir),
    }
