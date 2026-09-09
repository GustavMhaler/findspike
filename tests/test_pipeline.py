import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import FakeClient
from shanzhai.pipeline import compose_latest, scan_choch, scan_daily

UTC = timezone.utc


@pytest.fixture
def now():
    return datetime(2026, 8, 11, 4, 50, tzinfo=UTC)


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path / "state"


class TestScanDaily:
    def test_writes_spikes_sorted_by_ratio(self, now, state_dir):
        client = FakeClient(now, breakouts=set())
        result = scan_daily(client, state_dir, now)
        assert [s["symbol"] for s in result["spikes"]] == ["AAAUSDT", "CCCUSDT"]
        assert result["coverage"] == 1.0
        assert result["symbols_total"] == 4
        assert result["duration_seconds"] >= 0
        stored = json.loads((state_dir / "daily.json").read_text())
        assert stored["spikes"][0]["symbol"] == "AAAUSDT"
        assert stored["spikes"][0]["ratio"] == 8.0
        assert "watch_until" in stored["spikes"][0]

    def test_coverage_below_threshold_raises_and_writes_nothing(self, now, state_dir):
        client = FakeClient(now, breakouts=set(), fail_symbols={"BBBUSDT", "CCCUSDT", "DDDUSDT"})
        with pytest.raises(RuntimeError, match="coverage"):
            scan_daily(client, state_dir, now)
        assert not (state_dir / "daily.json").exists()


class TestScanCoach:
    def _seed_daily(self, state_dir, now):
        scan_daily(FakeClient(now, breakouts=set()), state_dir, now)

    def test_detects_breakout_and_sets_notify(self, now, state_dir):
        self._seed_daily(state_dir, now)
        client = FakeClient(now, breakouts={"AAAUSDT"})
        result = scan_choch(client, state_dir, now)
        assert result["new_signal_count"] == 2
        signals = result["signals"]
        assert [s["symbol"] for s in signals] == ["AAAUSDT", "AAAUSDT"]
        assert [s["level_tag"] for s in signals] == ["first", "second"]
        assert [s["email_ok"] for s in signals] == [False, True]
        assert [s["notify"] for s in signals] == [False, True]
        assert all(s["key"].startswith("AAAUSDT:swing:BOS:") for s in signals)
        assert len(signals[0]["trace"]) == 40
        assert result["data_candle_through"] is not None

    def test_second_breakout_crosses_digest_window(self, now, state_dir):
        """A second breakout older than the notify freshness window still gets
        email_ok (it is a 二次突破) but is not marked for immediate notify."""
        self._seed_daily(state_dir, now)
        client = FakeClient(now, breakouts={"AAAUSDT"})
        result = scan_choch(client, state_dir, now, seed=True)
        signals = result["signals"]
        assert [s["level_tag"] for s in signals] == ["first", "second"]
        assert [s["email_ok"] for s in signals] == [False, True]
        assert all(s["notify"] is False for s in signals)

    def test_idempotent_second_scan_sends_nothing(self, now, state_dir):
        self._seed_daily(state_dir, now)
        client = FakeClient(now, breakouts={"AAAUSDT"})
        first = scan_choch(client, state_dir, now)
        assert first["new_signal_count"] == 2
        second = scan_choch(client, state_dir, now)
        assert second["new_signal_count"] == 0
        history = json.loads((state_dir / "choch_history.json").read_text())
        assert len(history) == 2

    def test_watch_pool_coverage_gate(self, now, state_dir):
        self._seed_daily(state_dir, now)
        client = FakeClient(now, breakouts={"AAAUSDT"}, fail_symbols={"AAAUSDT", "CCCUSDT", "DDDUSDT"})
        with pytest.raises(RuntimeError, match="coverage"):
            scan_choch(client, state_dir, now)
        assert not (state_dir / "choch.json").exists()

    def test_empty_watch_pool_passes(self, now, state_dir):
        result = scan_choch(FakeClient(now, breakouts=set()), state_dir, now)
        assert result["watch_count"] == 0
        assert result["new_signal_count"] == 0

    def test_seed_records_history_without_notify(self, now, state_dir):
        self._seed_daily(state_dir, now)
        client = FakeClient(now, breakouts={"AAAUSDT"})
        result = scan_choch(client, state_dir, now, seed=True)
        assert result["new_signal_count"] == 2
        assert all(s["notify"] is False for s in result["signals"])
        assert result["notify_count"] == 0

    def test_watch_pool_respects_watch_until(self, now, state_dir):
        self._seed_daily(state_dir, now)
        client = FakeClient(now, breakouts={"AAAUSDT"})
        daily = json.loads((state_dir / "daily.json").read_text())
        for spike in daily["spikes"]:
            spike["watch_until"] = (now - timedelta(days=1)).date().isoformat()
        (state_dir / "daily.json").write_text(json.dumps(daily))
        result = scan_choch(client, state_dir, now)
        assert result["watch_count"] == 0
        assert result["new_signal_count"] == 0

    def test_no_daily_state_means_no_scan(self, now, state_dir):
        result = scan_choch(FakeClient(now, breakouts={"AAAUSDT"}), state_dir, now)
        assert result["watch_count"] == 0
        assert result["signals"] == []

    def test_rederived_duplicate_absorbed_not_reemitted(self, now, state_dir):
        """A structure re-derived with a drifted structure_time (new key but
        same symbol/layer/tag/level_tag/signal_time) must not be emitted again."""
        self._seed_daily(state_dir, now)
        client = FakeClient(now, breakouts={"AAAUSDT"})
        first = scan_choch(client, state_dir, now)
        real_keys = [s["key"] for s in first["signals"]]
        events = first["signals"]
        (state_dir / "choch_keys.json").write_text(json.dumps(["AAAUSDT:swing:BOS:9999999999"]))
        rewritten = []
        for i, event in enumerate(events):
            rewritten.append(
                {**event, "structure_time": f"2026-08-0{i+1}T00:00:00+00:00", "key": f"AAAUSDT:swing:BOS:999999999{i}"}
            )
        (state_dir / "choch_history.json").write_text(json.dumps(rewritten))
        second = scan_choch(client, state_dir, now)
        assert second["new_signal_count"] == 0
        keys = json.loads((state_dir / "choch_keys.json").read_text())
        assert all(k in keys for k in real_keys)
        history = json.loads((state_dir / "choch_history.json").read_text())
        assert len(history) == 2


class TestComposeLatest:
    def test_contract(self, now, state_dir):
        scan_daily(FakeClient(now, breakouts={"AAAUSDT"}), state_dir, now)
        scan_choch(FakeClient(now, breakouts={"AAAUSDT"}), state_dir, now)
        latest = compose_latest(state_dir, now)
        assert latest["schema_version"] == 2
        assert latest["algorithm_version"] == "volume-spike-v2+smc-ht1h-lt15m-first-second-v1"
        assert latest["status"] == "ok"
        assert latest["timezone"] == "Asia/Shanghai"
        assert latest["generated_at"] == now.isoformat()
        assert latest["data_candle_through"].startswith("2026-08-11")
        assert latest["runtime"]["coverage"] == 1.0
        assert "duration_seconds" in latest["runtime"]
        assert [s["symbol"] for s in latest["volume_spikes"]] == ["AAAUSDT", "CCCUSDT"]
        assert latest["choch"]["latest_scan_at"] == now.isoformat()
        assert latest["choch"]["new_signal_count"] == 2
        assert len(latest["choch"]["signals"]) == 2
        assert len(latest["choch"]["history"]) == 2

    def test_history_bounded_to_thirty_days(self, now, state_dir):
        scan_daily(FakeClient(now, breakouts={"AAAUSDT"}), state_dir, now)
        scan_choch(FakeClient(now, breakouts={"AAAUSDT"}), state_dir, now)
        history = json.loads((state_dir / "choch_history.json").read_text())
        history.append({**history[0], "signal_time": (now - timedelta(days=40)).isoformat()})
        (state_dir / "choch_history.json").write_text(json.dumps(history))
        latest = compose_latest(state_dir, now)
        assert len(latest["choch"]["history"]) == 2

    def test_empty_state_renders_safe_defaults(self, now, state_dir):
        latest = compose_latest(state_dir, now)
        assert latest["status"] == "ok"
        assert latest["volume_spikes"] == []
        assert latest["choch"]["signals"] == []
        assert latest["choch"]["history"] == []
        assert latest["data_candle_through"] is None
