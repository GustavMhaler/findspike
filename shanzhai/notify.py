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


def build_digest_payload(signals: list[dict], scan_at: datetime) -> dict:
    fresh = [
        s for s in signals
        if scan_at - datetime.fromisoformat(s["signal_time"]) <= DIGEST_MAX_AGE
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
            }
            for s in fresh
        ],
    }


def build_admin_alert_payload(command: str, message: str, at: datetime) -> dict:
    return {"kind": "admin_alert", "command": command, "message": message, "at": at.isoformat()}
