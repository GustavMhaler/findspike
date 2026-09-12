from datetime import datetime, timedelta, timezone

from shanzhai.notify import (
    DIGEST_MAX_AGE,
    build_digest_payload,
    select_immediate_signals,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


def _signal(key: str, signal_time: str, symbol: str = "AAAUSDT", **overrides) -> dict:
    signal = {
        "symbol": symbol, "key": key, "close": 0.14, "level": 0.13,
        "breakout_pct": 1.5, "signal_time": signal_time, "tag": "BOS",
        "direction": "bullish", "layer": "swing", "email_ok": True, "notify": True,
    }
    signal.update(overrides)
    return signal


class TestDigestPayload:
    def test_fresh_signal_included(self):
        fresh = _signal("AAAUSDT:swing:BOS:1", (NOW - timedelta(hours=2)).isoformat())
        payload = build_digest_payload([fresh], NOW)
        assert len(payload["signals"]) == 1
        assert payload["signals"][0]["key"] == fresh["key"]

    def test_stale_signals_excluded(self):
        stale_10d = _signal("BBBUSDT:swing:BOS:2", (NOW - timedelta(days=10)).isoformat(), "BBBUSDT")
        stale_27h = _signal("CCCUSDT:swing:BOS:3", (NOW - timedelta(hours=27)).isoformat(), "CCCUSDT")
        fresh = _signal("AAAUSDT:swing:BOS:4", (NOW - timedelta(hours=3)).isoformat())
        payload = build_digest_payload([stale_10d, stale_27h, fresh], NOW)
        assert [s["symbol"] for s in payload["signals"]] == ["AAAUSDT"]

    def test_bearish_excluded(self):
        bearish = _signal("BBBUSDT:swing:BOS:5", (NOW - timedelta(hours=2)).isoformat(), "BBBUSDT")
        bearish["direction"] = "bearish"
        bullish = _signal("AAAUSDT:swing:BOS:6", (NOW - timedelta(hours=1)).isoformat())
        payload = build_digest_payload([bearish, bullish], NOW)
        assert [s["symbol"] for s in payload["signals"]] == ["AAAUSDT"]

    def test_constant_matches_gate(self):
        assert DIGEST_MAX_AGE >= timedelta(hours=24)


class TestImmediateSelection:
    def test_only_notifiable_second_bullish_signals_are_selected(self):
        first = _signal(
            "AAAUSDT:swing:BOS:1",
            (NOW - timedelta(hours=2)).isoformat(),
            email_ok=False,
            notify=False,
        )
        bearish = _signal(
            "BBBUSDT:swing:BOS:2",
            (NOW - timedelta(hours=2)).isoformat(),
            "BBBUSDT",
            direction="bearish",
        )
        second = _signal("AAAUSDT:swing:BOS:3", (NOW - timedelta(hours=1)).isoformat())
        selected = select_immediate_signals([first, bearish, second], NOW)
        assert [s["key"] for s in selected] == [second["key"]]

    def test_stale_and_future_signals_are_excluded(self):
        stale = _signal("AAAUSDT:swing:BOS:1", (NOW - timedelta(hours=27)).isoformat())
        future = _signal("BBBUSDT:swing:BOS:2", (NOW + timedelta(minutes=1)).isoformat(), "BBBUSDT")
        selected = select_immediate_signals([stale, future], NOW)
        assert selected == []

    def test_selected_signals_are_sorted_by_time(self):
        older = _signal("AAAUSDT:swing:BOS:1", (NOW - timedelta(hours=3)).isoformat())
        newer = _signal("BBBUSDT:swing:BOS:2", (NOW - timedelta(hours=1)).isoformat(), "BBBUSDT")
        selected = select_immediate_signals([newer, older], NOW)
        assert [s["symbol"] for s in selected] == ["AAAUSDT", "BBBUSDT"]
