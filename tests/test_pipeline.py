import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import FakeClient
from shanzhai.pipeline import compose_latest, scan_coach, scan_daily

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
        result = scan_coach(client, state_dir, now)
        assert result["new_signal_count"] == 1
        signal = result["signals"][0]
        assert signal["symbol"] == "AAAUSDT"
        assert signal["notify"] is True
        assert signal["key"].startswith("AAAUSDT:4h:")
        assert len(signal["trace"]) == 40
        assert result["data_candle_through"] is not None

    def test_idempotent_second_scan_sends_nothing(self, now, state_dir):
        self._seed_daily(state_dir, now)
        client = FakeClient(now, breakouts={"AAAUSDT"})
        first = scan_coach(client, state_dir, now)
        assert first["new_signal_count"] == 1
        second = scan_coach(client, state_dir, now)
        assert second["new_signal_count"] == 0
        history = json.loads((state_dir / "coach_history.json").read_text())
        assert len(history) == 1

    def test_seed_records_history_without_notify(self, now, state_dir):
        self._seed_daily(state_dir, now)
        client = FakeClient(now, breakouts={"AAAUSDT"})
        result = scan_coach(client, state_dir, now, seed=True)
        assert result["new_signal_count"] == 1
        assert result["signals"][0]["notify"] is False
        assert result["notify_count"] == 0

    def test_watch_pool_respects_watch_until(self, now, state_dir):
        self._seed_daily(state_dir, now)
        client = FakeClient(now, breakouts={"AAAUSDT"})
        daily = json.loads((state_dir / "daily.json").read_text())
        for spike in daily["spikes"]:
            spike["watch_until"] = (now - timedelta(days=1)).date().isoformat()
        (state_dir / "daily.json").write_text(json.dumps(daily))
        result = scan_coach(client, state_dir, now)
        assert result["watch_count"] == 0
        assert result["new_signal_count"] == 0

    def test_no_daily_state_means_no_scan(self, now, state_dir):
        result = scan_coach(FakeClient(now, breakouts={"AAAUSDT"}), state_dir, now)
        assert result["watch_count"] == 0
        assert result["signals"] == []


class TestComposeLatest:
    def test_contract(self, now, state_dir):
        scan_daily(FakeClient(now, breakouts={"AAAUSDT"}), state_dir, now)
        scan_coach(FakeClient(now, breakouts={"AAAUSDT"}), state_dir, now)
        latest = compose_latest(state_dir, now)
        assert latest["schema_version"] == 1
        assert latest["algorithm_version"] == "volume-spike-v2+coach-4h-pivot-3x3-v1"
        assert latest["status"] == "ok"
        assert latest["timezone"] == "Asia/Shanghai"
        assert latest["generated_at"] == now.isoformat()
        assert latest["data_candle_through"].startswith("2026-08-11")
        assert latest["runtime"]["coverage"] == 1.0
        assert "duration_seconds" in latest["runtime"]
        assert [s["symbol"] for s in latest["volume_spikes"]] == ["AAAUSDT", "CCCUSDT"]
        assert latest["coach"]["latest_scan_at"] == now.isoformat()
        assert latest["coach"]["new_signal_count"] == 1
        assert len(latest["coach"]["signals"]) == 1
        assert len(latest["coach"]["history"]) == 1

    def test_history_bounded_to_thirty_days(self, now, state_dir):
        scan_daily(FakeClient(now, breakouts={"AAAUSDT"}), state_dir, now)
        scan_coach(FakeClient(now, breakouts={"AAAUSDT"}), state_dir, now)
        history = json.loads((state_dir / "coach_history.json").read_text())
        history.append({**history[0], "signal_time": (now - timedelta(days=40)).isoformat()})
        (state_dir / "coach_history.json").write_text(json.dumps(history))
        latest = compose_latest(state_dir, now)
        assert len(latest["coach"]["history"]) == 1

    def test_empty_state_renders_safe_defaults(self, now, state_dir):
        latest = compose_latest(state_dir, now)
        assert latest["status"] == "ok"
        assert latest["volume_spikes"] == []
        assert latest["coach"]["signals"] == []
        assert latest["coach"]["history"] == []
        assert latest["data_candle_through"] is None
