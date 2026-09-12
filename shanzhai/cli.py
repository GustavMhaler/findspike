"""Deterministic CLI entry points for scan, build and delivery.

Commands:

  daily   refresh the volume-spike universe and republish the site
  choch   evaluate newly closed 1h candles, republish, immediately deliver new signals
  demo    build a sample site from deterministic synthetic data (no network)
  check   exit non-zero when the published site is stale

Exit code is non-zero on any failure; a failed run never replaces the last
known good site.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .binance_api import BinancePublicClient
from .charts import spike_symbols, update_charts
from .domain import Candle, UTC
from .io_utils import read_json
from .notify import (
    build_admin_alert_payload,
    build_digest_payload,
    request_delivery,
    select_immediate_signals,
)
from .pipeline import compose_latest, read_status_sidecar, scan_choch, scan_daily, write_status_sidecar
from .review import call_llm, evaluate_pending, llm_config
from .site import build_site

ALERT_AFTER_CONSECUTIVE_FAILURES = 3
DAILY_STALE_AFTER = timedelta(hours=26)
COACH_STALE_AFTER = timedelta(hours=2)
ENV_WORKER_URL = "WORKER_URL"
ENV_DIGEST_SECRET = "DIGEST_SECRET"
ENV_TURNSTILE_SITEKEY = "TURNSTILE_SITEKEY"
ENV_BINANCE_BASE_URL = "BINANCE_BASE_URL"


def _client() -> BinancePublicClient:
    base_url = os.environ.get(ENV_BINANCE_BASE_URL)
    return BinancePublicClient(base_url=base_url) if base_url else BinancePublicClient()


def _site_key() -> str:
    return os.environ.get(ENV_TURNSTILE_SITEKEY, "").strip()


def _record_success(status: dict, command: str, state: Path, now: datetime) -> dict:
    status["consecutive_failures"] = 0
    status["last_error"] = None
    if command == "daily":
        status["last_daily_success"] = now.isoformat()
    elif command == "charts":
        status["last_charts_success"] = now.isoformat()
    else:
        status["last_choch_success"] = now.isoformat()
    status["last_build"] = now.isoformat()
    write_status_sidecar(state, status)
    return status


def _record_failure(status: dict, command: str, state: Path, now: datetime, message: str) -> dict:
    failures = status.get("consecutive_failures", 0) + 1
    status.update(
        consecutive_failures=failures,
        last_error={"command": command, "at": now.isoformat(), "message": message[:500]},
    )
    write_status_sidecar(state, status)
    return status


def _deliver(state: Path, payload: dict) -> dict:
    worker_url = os.environ.get(ENV_WORKER_URL, "https://shanzhai.shaojiang61.site").rstrip("/")
    secret = os.environ.get(ENV_DIGEST_SECRET, "").strip()
    now = datetime.now(UTC)
    if not secret:
        summary = {"sent": False, "error": "DIGEST_SECRET not configured; delivery skipped"}
    else:
        summary = request_delivery(worker_url, secret, payload)
    status = read_status_sidecar(state)
    status["last_notify"] = {"at": now.isoformat(), **summary}
    write_status_sidecar(state, status)
    return summary


def _maybe_admin_alert(state: Path, command: str, message: str) -> None:
    status = read_status_sidecar(state)
    if status.get("consecutive_failures", 0) < ALERT_AFTER_CONSECUTIVE_FAILURES:
        return
    payload = build_admin_alert_payload(command, message, datetime.now(UTC))
    summary = _deliver(state, payload)
    print(f"admin alert: {summary}", file=sys.stderr)


PENDING_NOTIFY_KEY = "pending_notifications"
EXPIRED_NOTIFY_KEY = "expired_notifications"


def _deliver_immediate(state: Path, signals: list[dict], now: datetime) -> dict:
    """Deliver new 二次突破 signals now, retrying a failed request next scan."""
    status = read_status_sidecar(state)
    pending = status.get(PENDING_NOTIFY_KEY, [])
    if not isinstance(pending, list):
        pending = []
    candidates = select_immediate_signals([*pending, *signals], now)
    if not candidates:
        if pending:
            expired = status.get(EXPIRED_NOTIFY_KEY, [])
            if not isinstance(expired, list):
                expired = []
            expired.extend(
                {
                    "key": item.get("key") if isinstance(item, dict) else None,
                    "signal_time": item.get("signal_time") if isinstance(item, dict) else None,
                    "expired_at": now.isoformat(),
                }
                for item in pending
            )
            status[EXPIRED_NOTIFY_KEY] = expired[-100:]
            status.pop(PENDING_NOTIFY_KEY, None)
            write_status_sidecar(state, status)
        return {"sent": False, "attempted": False, "error": "no new email-eligible signals"}

    # Persist the outbox before the network request. If the Worker is down or
    # the process exits after this point, the next hourly scan retries it.
    status[PENDING_NOTIFY_KEY] = candidates
    write_status_sidecar(state, status)
    summary = _deliver(state, build_digest_payload(candidates, now))
    summary["attempted"] = True
    if summary.get("sent"):
        status = read_status_sidecar(state)
        status.pop(PENDING_NOTIFY_KEY, None)
        write_status_sidecar(state, status)
    return summary


def _charts_dir(state: Path) -> Path:
    return state / "charts"


def cmd_daily(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    state = Path(args.state)
    try:
        client = _client()
        result = scan_daily(client, state, now, workers=args.workers)
        chart_summary = update_charts(
            client, spike_symbols(result), state, now, workers=args.workers
        )
        latest = compose_latest(state, now)
        build_site(Path(args.output), latest, site_key=_site_key(), charts_dir=_charts_dir(state))
        _record_success(read_status_sidecar(state), "daily", state, now)
        print(
            f"daily ok: {result['coverage']:.1%} coverage, "
            f"{len(result['spikes'])} spikes in {result['duration_seconds']}s; "
            f"charts {chart_summary['succeeded']}/{chart_summary['symbols']} "
            f"({len(chart_summary['failures'])} failed) in {chart_summary['duration_seconds']}s"
        )
        return 0
    except Exception as exc:
        _record_failure(read_status_sidecar(state), "daily", state, now, str(exc))
        _maybe_admin_alert(state, "daily", str(exc))
        print(f"daily failed: {exc}", file=sys.stderr)
        return 1


def cmd_choch(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    state = Path(args.state)
    try:
        result = scan_choch(_client(), state, now, seed=args.seed, workers=args.workers)
        latest = compose_latest(state, now)
        build_site(Path(args.output), latest, site_key=_site_key(), charts_dir=_charts_dir(state))
        _record_success(read_status_sidecar(state), "choch", state, now)
        print(
            f"choch ok: {result['watch_count']} watched, {result['new_signal_count']} new, "
            f"{result['notify_count']} notify in {result['duration_seconds']}s"
        )
        if not args.seed:
            summary = _deliver_immediate(state, result["signals"], now)
            if summary["attempted"]:
                print(f"immediate delivery: {summary}")
        return 0
    except Exception as exc:
        _record_failure(read_status_sidecar(state), "choch", state, now, str(exc))
        _maybe_admin_alert(state, "choch", str(exc))
        print(f"choch failed: {exc}", file=sys.stderr)
        return 1


def cmd_review(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    state = Path(args.state)
    config = llm_config()
    if config is None:
        print("review skipped: AI_API_KEY not configured (see deploy/shanzhai.env.example)")
        return 0
    try:
        summary = evaluate_pending(_client(), state, now, lambda prompt: call_llm(config, prompt))
        print(
            f"review ok: evaluated {summary['evaluated']}, "
            f"fetch failures {summary['fetch_failures']}, llm failures {summary['llm_failures']}"
        )
        if summary["evaluated"]:
            latest = compose_latest(state, now)
            build_site(Path(args.output), latest, site_key=_site_key(), charts_dir=_charts_dir(state))
            print(f"site rebuilt with {summary['evaluated']} new reviews")
        return 0
    except Exception as exc:
        print(f"review failed: {exc}", file=sys.stderr)
        return 1


def cmd_charts(args: argparse.Namespace) -> int:
    """Refresh daily chart payloads for the current spike symbols and republish."""
    now = datetime.now(UTC)
    state = Path(args.state)
    try:
        daily = read_json(state / "daily.json", {})
        symbols = spike_symbols(daily)
        summary = update_charts(_client(), symbols, state, now, workers=args.workers)
        latest = compose_latest(state, now)
        build_site(Path(args.output), latest, site_key=_site_key(), charts_dir=_charts_dir(state))
        _record_success(read_status_sidecar(state), "charts", state, now)
        print(
            f"charts ok: {summary['succeeded']}/{summary['symbols']} updated, "
            f"{len(summary['failures'])} failed in {summary['duration_seconds']}s"
        )
        return 0
    except Exception as exc:
        _record_failure(read_status_sidecar(state), "charts", state, now, str(exc))
        print(f"charts failed: {exc}", file=sys.stderr)
        return 1


def cmd_demo(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)
    client = _DemoClient(now)
    result = scan_daily(client, state, now, workers=args.workers)
    update_charts(client, spike_symbols(result), state, now, workers=args.workers)
    scan_choch(client, state, now, seed=True, workers=args.workers)
    latest = compose_latest(state, now)
    build_site(Path(args.output), latest, site_key=_site_key(), charts_dir=_charts_dir(state))
    print(f"demo ok: site built at {args.output}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    status_path = Path(args.output) / "status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"check failed: no status.json at {status_path}", file=sys.stderr)
        return 1
    now = datetime.now(UTC)
    generated = datetime.fromisoformat(status.get("generated_at", ""))
    through = status.get("data_candle_through")
    through = datetime.fromisoformat(through) if through else None
    stale = now - generated > DAILY_STALE_AFTER
    data_stale = through is not None and now - through > COACH_STALE_AFTER
    if stale or data_stale:
        print(f"check stale: generated {generated.isoformat()} (site), data through {through.isoformat() if through else 'n/a'}")
        return 1
    print(f"check ok: site generated {generated.isoformat()}, data through {through.isoformat() if through else 'n/a'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shanzhai", description="Shanzhai signal desk CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_daily = sub.add_parser("daily", help="refresh volume-spike universe and republish")
    p_daily.add_argument("--output", default="public")
    p_daily.add_argument("--state", default="state")
    p_daily.add_argument("--workers", type=int, default=6)
    p_daily.set_defaults(func=cmd_daily)

    p_choch = sub.add_parser("choch", help="evaluate closed 1h candles for BOS/CHoCH, republish, deliver digest")
    p_choch.add_argument("--output", default="public")
    p_choch.add_argument("--state", default="state")
    p_choch.add_argument("--workers", type=int, default=6)
    p_choch.add_argument("--seed", action="store_true", help="record history without sending anything")
    p_choch.set_defaults(func=cmd_choch)

    p_review = sub.add_parser("review", help="evaluate signals matured 24h ago via AI and republish")
    p_review.add_argument("--output", default="public")
    p_review.add_argument("--state", default="state")
    p_review.set_defaults(func=cmd_review)

    p_charts = sub.add_parser("charts", help="refresh daily chart payloads for spike symbols and republish")
    p_charts.add_argument("--output", default="public")
    p_charts.add_argument("--state", default="state")
    p_charts.add_argument("--workers", type=int, default=6)
    p_charts.set_defaults(func=cmd_charts)

    p_demo = sub.add_parser("demo", help="build a sample site from synthetic data")
    p_demo.add_argument("--output", default="public")
    p_demo.add_argument("--state", default="state")
    p_demo.add_argument("--workers", type=int, default=6)
    p_demo.set_defaults(func=cmd_demo)

    p_check = sub.add_parser("check", help="fail when the published site is stale")
    p_check.add_argument("--output", default="public")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


class _DemoClient(BinancePublicClient):
    """Deterministic fake Binance client: no network, fixed output."""

    def __init__(self, now: datetime):
        self.now = now

    def usdt_symbols(self) -> list[str]:
        return ["BTCUSDT", "ETHUSDT", "SPKUSDT", "WAVYUSDT", "FLATUSDT", "QUIETUSDT"]

    def candles(
        self, symbol: str, interval: str, limit: int,
        start_time: datetime | None = None, end_time: datetime | None = None,
    ) -> list[Candle]:
        if interval == "1d":
            return self._daily(symbol)
        if interval == "4h":
            return self._ht4h(symbol)
        if interval == "15m":
            return self._lt15m(symbol)
        return self._hourly(symbol)

    def _daily(self, symbol: str) -> list[Candle]:
        today = self.now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        pattern = {"SPKUSDT": 8.0, "WAVYUSDT": 5.2, "FLATUSDT": 6.0}.get(symbol, 1.0)
        candles = []
        for i in range(17, 0, -1):
            open_t = today - timedelta(days=i)
            volume = pattern if i == 1 else 1.0
            close = 100.0 + (17 - i)
            candles.append(
                Candle(
                    open_t, open_t + timedelta(days=1),
                    open=close - 2, high=close + 3, low=close - 4, close=close,
                    volume=volume, quote_volume=volume * close * 100,
                )
            )
        return candles

    def _ht4h(self, symbol: str) -> list[Candle]:
        """Deterministic 4h structure: a pivot high of 110 flanked by lower
        highs so pivot size 3 confirms it, leaving the level actionable.
        """
        pattern = symbol == "FLATUSDT"
        boundary = self.now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        candles = []
        for i in range(80, 0, -1):
            close_t = boundary - timedelta(hours=4 * (i - 1))
            open_t = close_t - timedelta(hours=4)
            open_, high, low, close, volume = 101.0, 104.0, 98.0, 102.0, 100.0
            if pattern and i == 40:  # pivot high candidate (110), flanked by lower highs
                open_, high, low, close = 105.0, 110.0, 100.0, 106.0
            candles.append(
                Candle(open_t, close_t, open_, high, low, close, volume=volume, quote_volume=volume * 100.0)
            )
        return candles

    def _lt15m(self, symbol: str) -> list[Candle]:
        """Deterministic 15m trigger window: closes 100 until two crossing
        candles — one closes 112 (first breakout of 110) and the last closes
        118 (second breakout / 二次突破 of the upgraded 115 level).
        """
        pattern = symbol == "FLATUSDT"
        boundary = self.now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        candles = []
        for i in range(1000, 0, -1):
            close_t = boundary - timedelta(minutes=15 * (i - 1))
            open_t = close_t - timedelta(minutes=15)
            open_, high, low, close, volume = 100.0, 104.0, 98.0, 100.0, 100.0
            if pattern:
                if i == 201:  # index 799 — crosses 110 (first breakout)
                    open_, high, low, close, volume = 108.0, 115.0, 106.0, 112.0, 250.0
                elif i == 21:  # index 979 — crosses 115 (second breakout)
                    open_, high, low, close, volume = 114.0, 120.0, 112.0, 118.0, 250.0
            candles.append(
                Candle(open_t, close_t, open_, high, low, close, volume=volume, quote_volume=volume * 100.0)
            )
        return candles

    def _hourly(self, symbol: str) -> list[Candle]:
        """Deterministic 1h series used by the review module's 24h measurement
        and as the 1h structure feed.

        Two right-confirmed pivot highs: 110 at index 15 and a higher 115 at
        index 60. The 115 upgrades the active level once it confirms, so the 15m
        trigger series (above) fires a first breakout at 110 and a second
        (二次突破) at 115.
        """
        pattern = symbol == "FLATUSDT"
        boundary = self.now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        candles = []
        for i in range(80, 0, -1):
            close_t = boundary - timedelta(hours=i - 1)
            open_t = close_t - timedelta(hours=1)
            open_, high, low, close, volume = 101.0, 105.0, 99.0, 102.0, 100.0
            if pattern:
                if i == 65:  # index 15 — 1h pivot high (110)
                    open_, high, low, close = 104.0, 110.0, 102.0, 105.0
                elif i == 20:  # index 60 — higher pivot high (115)
                    open_, high, low, close = 109.0, 115.0, 107.0, 110.0
                elif i == 75:  # index 5 — deep low
                    open_, high, low, close = 95.0, 100.0, 80.0, 96.0
            candles.append(
                Candle(open_t, close_t, open_, high, low, close, volume=volume, quote_volume=volume * 100.0)
            )
        return candles


if __name__ == "__main__":
    sys.exit(main())
