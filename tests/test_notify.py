from datetime import datetime, timedelta, timezone

from shanzhai.notify import (
    DIGEST_MAX_AGE,
    PUSH_COOLDOWN,
    build_digest_payload,
    collect_digest,
    digest_due,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


def _signal(key: str, signal_time: str, symbol: str = "AAAUSDT", **overrides) -> dict:
    signal = {
        "symbol": symbol, "key": key, "close": 0.14, "level": 0.13,
        "breakout_pct": 1.5, "signal_time": signal_time, "tag": "BOS",
        "direction": "bullish", "layer": "swing", "email_ok": True,
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


class TestDigestCollection:
    def test_only_email_ok_and_bullish_collected(self):
        weak = _signal("AAAUSDT:swing:BOS:1", (NOW - timedelta(hours=2)).isoformat(), email_ok=False)
        bearish = _signal("BBBUSDT:swing:BOS:2", (NOW - timedelta(hours=2)).isoformat(), "BBBUSDT", direction="bearish")
        good = _signal("AAAUSDT:swing:BOS:3", (NOW - timedelta(hours=1)).isoformat())
        collected = collect_digest([weak, bearish, good], None, NOW)
        assert [s["key"] for s in collected] == [good["key"]]

    def test_since_last_digest(self):
        older = _signal("AAAUSDT:swing:BOS:1", (NOW - timedelta(hours=3)).isoformat())
        newer = _signal("BBBUSDT:swing:BOS:2", (NOW - timedelta(hours=1)).isoformat(), "BBBUSDT")
        last = NOW - timedelta(hours=2)
        collected = collect_digest([older, newer], last, NOW)
        assert [s["symbol"] for s in collected] == ["BBBUSDT"]

    def test_cooldown_collapses_same_symbol_direction(self):
        a = _signal("AAAUSDT:swing:BOS:1", (NOW - timedelta(hours=3)).isoformat(), breakout_pct=2.0)
        b = _signal("AAAUSDT:swing:BOS:2", (NOW - timedelta(hours=1)).isoformat(), breakout_pct=5.0)
        collected = collect_digest([a, b], None, NOW)
        assert len(collected) == 1
        assert collected[0]["breakout_pct"] == 5.0

    def test_cooldown_allows_different_directions(self):
        up = _signal("AAAUSDT:swing:BOS:1", (NOW - timedelta(hours=3)).isoformat())
        down = _signal("AAAUSDT:swing:CHoCH:2", (NOW - timedelta(hours=1)).isoformat(), direction="bearish")
        collected = collect_digest([up, down], None, NOW)
        assert len(collected) == 1  # bearish excluded entirely; only bullish email
        up2 = _signal("CCCUSDT:swing:BOS:4", (NOW - timedelta(hours=1)).isoformat(), "CCCUSDT")
        collected = collect_digest([up, up2], None, NOW)
        assert [s["symbol"] for s in collected] == ["AAAUSDT", "CCCUSDT"]

    def test_stale_signals_excluded(self):
        stale = _signal("AAAUSDT:swing:BOS:1", (NOW - timedelta(hours=30)).isoformat())
        fresh = _signal("BBBUSDT:swing:BOS:2", (NOW - timedelta(hours=1)).isoformat(), "BBBUSDT")
        collected = collect_digest([stale, fresh], None, NOW)
        assert [s["symbol"] for s in collected] == ["BBBUSDT"]

    def test_digest_due_checks_shanghai_hour(self):
        # 09:00 UTC = 17:00 Asia/Shanghai
        assert digest_due(NOW, 17)
        assert not digest_due(NOW, 8)

    def test_pushes_are_sorted_by_time(self):
        a = _signal("AAAUSDT:swing:BOS:1", (NOW - timedelta(hours=3)).isoformat())
        b = _signal("BBBUSDT:swing:BOS:2", (NOW - timedelta(hours=1)).isoformat(), "BBBUSDT")
        collected = collect_digest([b, a], None, NOW)
        assert [s["symbol"] for s in collected] == ["AAAUSDT", "BBBUSDT"]

    def test_cooldown_constant_is_24h(self):
        assert PUSH_COOLDOWN == timedelta(hours=24)
