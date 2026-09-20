import json
from datetime import datetime, timedelta, timezone

import pytest

import shanzhai.cli as cli
from conftest import FakeClient

UTC = timezone.utc


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient(datetime.now(UTC), breakouts={"AAAUSDT"})
    monkeypatch.setattr(cli, "_client", lambda: client)
    return client


def test_demo_builds_deterministic_site(tmp_path):
    output = tmp_path / "public"
    state = tmp_path / "state"
    assert cli.main(["demo", "--output", str(output), "--state", str(state)]) == 0
    latest = json.loads((output / "data" / "latest.json").read_text())
    symbols = [s["symbol"] for s in latest["volume_spikes"]]
    assert "SPKUSDT" in symbols
    assert [s["symbol"] for s in latest["choch"]["signals"]] == ["FLATUSDT", "FLATUSDT"]
    assert [s["level_tag"] for s in latest["choch"]["signals"]] == ["first", "second"]
    assert all(s["symbol"] == "FLATUSDT" for s in latest["choch"]["history"])
    html = (output / "index.html").read_text()
    assert "FLATUSDT" in html and "spark-pivot" in html


def test_daily_and_choch_end_to_end(tmp_path, fake_client):
    output = tmp_path / "public"
    state = tmp_path / "state"
    assert cli.main(["daily", "--output", str(output), "--state", str(state)]) == 0
    assert cli.main(["choch", "--output", str(output), "--state", str(state)]) == 0
    latest = json.loads((output / "data" / "latest.json").read_text())
    assert latest["runtime"]["coverage"] == 1.0
    assert latest["choch"]["new_signal_count"] == 2
    assert all(s["symbol"] == "AAAUSDT" for s in latest["choch"]["signals"])
    status = json.loads((state / "status.json").read_text())
    assert status["consecutive_failures"] == 0
    assert status["last_daily_success"] is not None
    assert status["last_choch_success"] is not None
    chart = json.loads((output / "charts" / "AAAUSDT.json").read_text())
    assert chart["symbol"] == "AAAUSDT" and chart["intervals"]["1d"]["candles"]


def test_second_breakout_is_delivered_immediately(tmp_path, fake_client, monkeypatch):
    output = tmp_path / "public"
    state = tmp_path / "state"
    deliveries = []

    # The old implementation only delivered during this configured hour. An
    # immediate signal must not depend on the daily digest slot.
    shanghai_hour = datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).hour
    monkeypatch.setenv("DIGEST_HOUR", str((shanghai_hour + 1) % 24))
    monkeypatch.setattr(
        cli,
        "_deliver",
        lambda _state, payload: deliveries.append(payload) or {"sent": True},
    )

    assert cli.main(["daily", "--output", str(output), "--state", str(state)]) == 0
    assert cli.main(["choch", "--output", str(output), "--state", str(state)]) == 0

    assert len(deliveries) == 1
    assert [signal["level_tag"] for signal in deliveries[0]["signals"]] == ["second"]
    assert deliveries[0]["signals"][0]["symbol"] == "AAAUSDT"
    # The fixture's first breakout is 50h old, so only the fresh second
    # breakout is eligible in this run. A first breakout is covered below at
    # the immediate-delivery seam where it is still within the 26h window.
    assert [signal["level_tag"] for signal in deliveries[0]["qq_signals"]] == ["second"]


def test_first_breakout_is_delivered_to_qq_but_not_email(tmp_path, monkeypatch):
    state = tmp_path / "state"
    now = datetime.now(UTC)
    signal = {
        "symbol": "AAAUSDT", "key": "AAAUSDT:first", "close": 112,
        "level": 110, "breakout_pct": 1.8, "signal_time": (now - timedelta(hours=1)).isoformat(),
        "tag": "BOS", "direction": "bullish", "layer": "swing", "level_tag": "first",
        "email_ok": False, "notify": False,
    }
    deliveries = []
    monkeypatch.setattr(
        cli,
        "_deliver",
        lambda _state, payload: deliveries.append(payload) or {"sent": True},
    )

    summary = cli._deliver_immediate(state, [signal], now)

    assert summary["attempted"] is True
    assert deliveries[0]["signals"] == []
    assert [item["key"] for item in deliveries[0]["qq_signals"]] == [signal["key"]]


def test_failed_immediate_delivery_is_retried_on_next_scan(tmp_path, fake_client, monkeypatch):
    output = tmp_path / "public"
    state = tmp_path / "state"
    deliveries = []
    outcomes = iter([{"sent": False, "error": "TimeoutError"}, {"sent": True}])
    monkeypatch.setattr(
        cli,
        "_deliver",
        lambda _state, payload: deliveries.append(payload) or next(outcomes),
    )

    assert cli.main(["daily", "--output", str(output), "--state", str(state)]) == 0
    assert cli.main(["choch", "--output", str(output), "--state", str(state)]) == 0
    status = json.loads((state / "status.json").read_text())
    assert status["pending_notifications"]
    assert status["pending_qq_notifications"]

    # The scan is idempotent, but the failed signal remains in the private
    # outbox and is retried even though no new signal is found.
    assert cli.main(["choch", "--output", str(output), "--state", str(state)]) == 0
    assert len(deliveries) == 2
    status = json.loads((state / "status.json").read_text())
    assert "pending_notifications" not in status
    assert "pending_qq_notifications" not in status


def test_expired_pending_delivery_is_recorded(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    status = {
        "consecutive_failures": 0,
        "pending_notifications": [{"key": "AAA:old", "signal_time": old}],
    }
    (state / "status.json").write_text(json.dumps(status))

    summary = cli._deliver_immediate(state, [], datetime.now(UTC))

    assert summary["attempted"] is False
    saved = json.loads((state / "status.json").read_text())
    assert "pending_notifications" not in saved
    assert saved["expired_notifications"][0]["key"] == "AAA:old"


def test_charts_command_updates_payloads_and_republishes(tmp_path, fake_client):
    output = tmp_path / "public"
    state = tmp_path / "state"
    assert cli.main(["daily", "--output", str(output), "--state", str(state)]) == 0
    (state / "charts" / "AAAUSDT.json").unlink()
    assert cli.main(["charts", "--output", str(output), "--state", str(state)]) == 0
    chart = json.loads((output / "charts" / "AAAUSDT.json").read_text())
    assert chart["symbol"] == "AAAUSDT" and chart["intervals"]["1d"]["candles"]
    status = json.loads((state / "status.json").read_text())
    assert status["last_charts_success"] is not None


def test_second_choch_run_is_idempotent(tmp_path, fake_client):
    output = tmp_path / "public"
    state = tmp_path / "state"
    cli.main(["daily", "--output", str(output), "--state", str(state)])
    cli.main(["choch", "--output", str(output), "--state", str(state)])
    assert cli.main(["choch", "--output", str(output), "--state", str(state)]) == 0
    latest = json.loads((output / "data" / "latest.json").read_text())
    assert latest["choch"]["new_signal_count"] == 0
    assert len(latest["choch"]["history"]) == 2


def test_digest_skipped_without_secret(tmp_path, fake_client, monkeypatch):
    output = tmp_path / "public"
    state = tmp_path / "state"
    monkeypatch.delenv("DIGEST_SECRET", raising=False)
    monkeypatch.setenv("WORKER_URL", "https://worker.test")
    monkeypatch.setenv("DIGEST_HOUR", str(datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).hour))
    cli.main(["daily", "--output", str(output), "--state", str(state)])
    assert cli.main(["choch", "--output", str(output), "--state", str(state)]) == 0
    status = json.loads((state / "status.json").read_text())
    assert "DIGEST_SECRET" in status["last_notify"]["error"]


def test_failure_records_status_sidecar(tmp_path, monkeypatch):
    state = tmp_path / "state"

    def failing_client():
        raise RuntimeError("no network")

    monkeypatch.setattr(cli, "_client", failing_client)
    code = cli.main(["daily", "--output", str(tmp_path / "public"), "--state", str(state)])
    assert code == 1
    status = json.loads((state / "status.json").read_text())
    assert status["consecutive_failures"] == 1
    assert status["last_error"]["command"] == "daily"
    assert not (tmp_path / "public").exists()


def test_admin_alert_after_consecutive_failures(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("DIGEST_SECRET", "s3cret")
    monkeypatch.setenv("WORKER_URL", "https://worker.test")
    monkeypatch.setattr(cli, "_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    codes = [cli.main(["daily", "--output", str(tmp_path / "public"), "--state", str(state)]) for _ in range(3)]
    assert codes == [1, 1, 1]
    status = json.loads((state / "status.json").read_text())
    assert status["consecutive_failures"] == 3
    assert status["last_notify"]["sent"] is False


def test_check_fails_for_missing_site(tmp_path):
    assert cli.main(["check", "--output", str(tmp_path / "nope")]) == 1


def test_check_fails_for_stale_site(tmp_path):
    output = tmp_path / "public"
    cli.main(["demo", "--output", str(output), "--state", str(tmp_path / "state")])
    status_path = output / "status.json"
    status = json.loads(status_path.read_text())
    status["generated_at"] = "2026-01-01T00:00:00+00:00"
    status["data_candle_through"] = "2026-01-01T00:00:00+00:00"
    status_path.write_text(json.dumps(status))
    assert cli.main(["check", "--output", str(output)]) == 1
