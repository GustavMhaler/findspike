"""Digest and admin-alert delivery requests, sent to the Worker after a build.

Publication must never depend on delivery: every function here returns a
summary dict instead of raising, so a failing Worker/Resend only marks the
notification as failed in the status sidecar.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta


def request_delivery(
    worker_url: str,
    secret: str,
    payload: dict,
    timeout: float = 15,
) -> dict:
    """POST a delivery job to the Worker's /api/deliver endpoint."""
    body = json.dumps({"secret": secret, **payload}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{worker_url.rstrip('/')}/api/deliver",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "shanzhai-signal-desk/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return {"sent": True, "delivered": json.load(response)}
    except Exception as exc:
        return {"sent": False, "error": type(exc).__name__}


# Only signals triggered recently may be emailed; re-derived copies of old
# structures (structure-time drift) must never reach subscribers' inboxes.
DIGEST_MAX_AGE = timedelta(hours=26)

# Both email and QQ subscribers receive bullish signals only.
DIGEST_DIRECTIONS = ("bullish",)
QQ_DIRECTIONS = ("bullish",)
BREAKOUT_LEVEL_TAGS = ("first", "second")
REQUIRED_DELIVERY_FIELDS = (
    "symbol", "key", "close", "level", "breakout_pct", "signal_time",
    "tag", "direction", "layer", "level_tag",
)


def _signal_payload(signal: dict) -> dict:
    return {
        "symbol": signal["symbol"], "key": signal["key"], "close": signal["close"],
        "level": signal["level"], "breakout_pct": signal["breakout_pct"],
        "signal_time": signal["signal_time"], "tag": signal["tag"],
        "direction": signal["direction"], "layer": signal["layer"],
        "level_tag": signal.get("level_tag", "small"),
        "email_ok": signal.get("email_ok", False), "notify": signal.get("notify", False),
    }


def build_digest_payload(signals: list[dict], scan_at: datetime) -> dict:
    fresh = [
        s for s in signals
        if s.get("direction") in DIGEST_DIRECTIONS
        and scan_at - datetime.fromisoformat(s["signal_time"]) <= DIGEST_MAX_AGE
    ]
    return {
        "kind": "digest",
        "scan_at": scan_at.isoformat(),
        "signals": [_signal_payload(s) for s in fresh],
    }


def select_immediate_signals(
    signals: list[dict],
    now: datetime,
    max_age: timedelta = DIGEST_MAX_AGE,
) -> list[dict]:
    """Select fresh 二次突破 signals from the current scan for delivery.

    ``notify`` is assigned by the pipeline only to the second (or later)
    bullish breakout, after the closed-candle and freshness checks. The
    delivery boundary repeats the important gates so a malformed or manually
    restored state cannot email a first breakout.
    """
    cutoff = now - max_age
    selected = []
    seen_keys = set()
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        if not signal.get("notify") or not signal.get("email_ok"):
            continue
        if signal.get("level_tag") != "second":
            continue
        if any(field not in signal for field in REQUIRED_DELIVERY_FIELDS):
            continue
        key = signal.get("key")
        if key and key in seen_keys:
            continue
        if signal.get("direction") not in DIGEST_DIRECTIONS:
            continue
        try:
            signal_time = datetime.fromisoformat(signal["signal_time"])
        except (KeyError, ValueError):
            continue
        if signal_time <= cutoff or signal_time > now:
            continue
        if key:
            seen_keys.add(key)
        selected.append(signal)
    return sorted(selected, key=lambda s: s["signal_time"])


def select_qq_signals(
    signals: list[dict],
    now: datetime,
    max_age: timedelta = DIGEST_MAX_AGE,
) -> list[dict]:
    """Select fresh first/second breakout signals for QQ delivery.

    QQ receives the first breakout that is intentionally dashboard-only for
    email, as well as the second breakout. Bearish signals remain dashboard-
    only and are excluded from all outbound notifications.
    """
    cutoff = now - max_age
    selected = []
    seen_keys = set()
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        if signal.get("direction") not in QQ_DIRECTIONS:
            continue
        if signal.get("layer") != "swing" or signal.get("level_tag") not in BREAKOUT_LEVEL_TAGS:
            continue
        if any(field not in signal for field in REQUIRED_DELIVERY_FIELDS):
            continue
        key = signal.get("key")
        if key and key in seen_keys:
            continue
        try:
            signal_time = datetime.fromisoformat(signal["signal_time"])
        except (KeyError, ValueError):
            continue
        if signal_time <= cutoff or signal_time > now:
            continue
        if key:
            seen_keys.add(key)
        selected.append(signal)
    return sorted(selected, key=lambda s: s["signal_time"])


def build_qq_signals(signals: list[dict], scan_at: datetime) -> list[dict]:
    """Build the QQ portion of a delivery request, including first breakouts."""
    return [_signal_payload(s) for s in select_qq_signals(signals, scan_at)]


def build_admin_alert_payload(command: str, message: str, at: datetime) -> dict:
    return {"kind": "admin_alert", "command": command, "message": message, "at": at.isoformat()}
