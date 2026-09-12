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

# Subscribers opted out of bearish signals; digests are bullish-only.
DIGEST_DIRECTIONS = ("bullish",)

def build_digest_payload(signals: list[dict], scan_at: datetime) -> dict:
    fresh = [
        s for s in signals
        if s.get("direction") in DIGEST_DIRECTIONS
        and scan_at - datetime.fromisoformat(s["signal_time"]) <= DIGEST_MAX_AGE
    ]
    return {
        "kind": "digest",
        "scan_at": scan_at.isoformat(),
        "signals": [
            {
                "symbol": s["symbol"], "key": s["key"], "close": s["close"],
                "level": s["level"], "breakout_pct": s["breakout_pct"],
                "signal_time": s["signal_time"], "tag": s["tag"],
                "direction": s["direction"], "layer": s["layer"],
                "level_tag": s.get("level_tag", "small"),
            }
            for s in fresh
        ],
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


def build_admin_alert_payload(command: str, message: str, at: datetime) -> dict:
    return {"kind": "admin_alert", "command": command, "message": message, "at": at.isoformat()}
