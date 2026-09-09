"""Digest and admin-alert delivery requests, sent to the Worker after a build.

Publication must never depend on delivery: every function here returns a
summary dict instead of raising, so a failing Worker/Resend only marks the
notification as failed in the status sidecar.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone


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

# Per-symbol cooldown: the same symbol is pushed at most once per direction
# within this window, so back-to-back signals from one pair collapse into a
# single email entry instead of flooding the inbox.
PUSH_COOLDOWN = timedelta(hours=24)
SHANGHAI = timezone(timedelta(hours=8))


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


def digest_due(now: datetime, hour: int) -> bool:
    """True when the current Asia/Shanghai wall-clock hour is the digest slot."""
    return now.astimezone(SHANGHAI).hour == hour


def collect_digest(
    history: list[dict],
    last_digest_at: datetime | None,
    now: datetime,
    max_age: timedelta = DIGEST_MAX_AGE,
    cooldown: timedelta = PUSH_COOLDOWN,
) -> list[dict]:
    """Select signals for the next digest.

    Rules (delivery-side selection):
    - only ``email_ok`` signals are considered — these are the bullish signals
      that are the symbol's SECOND (or later) breakout within the recent window,
      i.e. 二次突破;
    - bullish-only and newer than ``max_age``;
    - only signals that fired after the previous digest, if any;
    - at most one signal per (symbol, direction) per ``cooldown`` — the entry
      with the highest ``breakout_pct`` wins, so a pair's run collapses into a
      single email row.
    """
    cutoff = now - max_age
    best: dict[tuple[str, str], dict] = {}
    for signal in history:
        if not signal.get("email_ok"):
            continue
        if signal.get("direction") not in DIGEST_DIRECTIONS:
            continue
        signal_time = datetime.fromisoformat(signal["signal_time"])
        if signal_time <= cutoff or signal_time > now:
            continue
        if last_digest_at is not None and signal_time <= last_digest_at:
            continue
        pair = (signal["symbol"], signal["direction"])
        if pair not in best or signal.get("breakout_pct", 0) > best[pair].get("breakout_pct", 0):
            best[pair] = signal
    return sorted(best.values(), key=lambda s: s["signal_time"])


def build_admin_alert_payload(command: str, message: str, at: datetime) -> dict:
    return {"kind": "admin_alert", "command": command, "message": message, "at": at.isoformat()}
