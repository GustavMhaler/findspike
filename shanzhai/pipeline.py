from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from .binance_api import BinancePublicClient
from .domain import closed_candles, newest_volume_spike
from .smc import scan_smc

ALGORITHM_VERSION = "volume-spike-v2+smc-swing50-internal5-bos-choch-v1"


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


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
        candles = closed_candles(client.candles(item["symbol"], "4h", 180), now)
        events = scan_smc(candles)
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
                    "trace": [round(c.close, 8) for c in candles[-40:]],
                    "key": key,
                }
            )
        return result, candles

    with ThreadPoolExecutor(max_workers=workers) as executor:
        jobs = {executor.submit(scan, item): item["symbol"] for item in active}
        through = None
        for job in as_completed(jobs):
            try:
                events, candles = job.result()
                if candles:
                    through = max(through, candles[-1].close_time) if through else candles[-1].close_time
                for signal in events:
                    if signal["key"] not in sent:
                        signal["notify"] = not seed and now - datetime.fromisoformat(signal["signal_time"]) <= timedelta(hours=24)
                        signals.append(signal)
                        sent.add(signal["key"])
            except Exception as exc:
                failures.append({"symbol": jobs[job], "error": type(exc).__name__})

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
    history = [item for item in history if datetime.fromisoformat(item["signal_time"]) >= cutoff]
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
    }
