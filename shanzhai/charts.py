"""Daily candlestick chart payloads for the volume-spike hover preview.

Charts are fetched once per day alongside the daily scan and stored under
``state/charts/<SYMBOL>.json``; every site build copies them into
``public/charts/`` so the page can lazy-load them when a spike symbol is
hovered. Chart payloads are auxiliary: per-symbol failures never fail the
daily scan, and candles are closed-only (anti-repaint by design).
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .binance_api import BinancePublicClient
from .domain import UTC, closed_candles
from .io_utils import write_json

CHART_INTERVAL = "1d"
CHART_CANDLES = 90


def chart_path(state_dir: Path, symbol: str) -> Path:
    return Path(state_dir) / "charts" / f"{symbol}.json"


def fetch_chart(client: BinancePublicClient, symbol: str, now: datetime) -> dict:
    candles = closed_candles(client.candles(symbol, CHART_INTERVAL, CHART_CANDLES), now)
    return {
        "symbol": symbol,
        "interval": CHART_INTERVAL,
        "updated_at": now.astimezone(UTC).isoformat(),
        "candles": [
            {
                "t": int(c.open_time.timestamp() * 1000),
                "o": round(c.open, 8),
                "h": round(c.high, 8),
                "l": round(c.low, 8),
                "c": round(c.close, 8),
                "v": round(c.volume, 8),
                "q": round(c.quote_volume, 8),
            }
            for c in candles
        ],
    }


def update_charts(
    client: BinancePublicClient,
    symbols: list[str],
    state_dir: Path,
    now: datetime,
    workers: int = 6,
) -> dict:
    """Fetch and persist chart payloads for ``symbols``; returns a summary.

    Failures are collected instead of raised so the daily scan and site build
    still succeed when a single symbol's chart fetch fails.
    """
    started = time.monotonic()
    succeeded = 0
    empty = 0
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        jobs = {executor.submit(fetch_chart, client, symbol, now): symbol for symbol in symbols}
        for job in as_completed(jobs):
            symbol = jobs[job]
            try:
                payload = job.result()
                if payload["candles"]:
                    write_json(chart_path(state_dir, symbol), payload)
                    succeeded += 1
                else:
                    empty += 1
            except Exception as exc:
                failures.append({"symbol": symbol, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    return {
        "symbols": len(symbols),
        "succeeded": succeeded,
        "empty": empty,
        "failures": failures,
        "duration_seconds": round(time.monotonic() - started, 2),
    }


def spike_symbols(daily: dict) -> list[str]:
    """Symbols in the current volume-spike universe, in table order."""
    seen: list[str] = []
    for spike in daily.get("spikes", []):
        symbol = spike.get("symbol")
        if symbol and symbol not in seen:
            seen.append(symbol)
    return seen
