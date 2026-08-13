"""Digest and admin-alert delivery requests, sent to the Worker after a build.

Publication must never depend on delivery: every function here returns a
summary dict instead of raising, so a failing Worker/Resend only marks the
notification as failed in the status sidecar.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime


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


def build_digest_payload(signals: list[dict], scan_at: datetime) -> dict:
    return {
        "kind": "digest",
        "scan_at": scan_at.isoformat(),
        "signals": [
            {
                "symbol": s["symbol"], "key": s["key"], "close": s["close"],
                "pivot_price": s["pivot_price"], "breakout_pct": s["breakout_pct"],
                "signal_time": s["signal_time"],
            }
            for s in signals
        ],
    }


def build_admin_alert_payload(command: str, message: str, at: datetime) -> dict:
    return {"kind": "admin_alert", "command": command, "message": message, "at": at.isoformat()}
