import json
from pathlib import Path

import pytest

from shanzhai.site import build_site

LATEST = {
    "schema_version": 2,
    "algorithm_version": "v-test",
    "generated_at": "2026-08-11T04:00:00+00:00",
    "timezone": "Asia/Shanghai",
    "status": "ok",
    "data_candle_through": "2026-08-11T04:00:00+00:00",
    "runtime": {"coverage": 1.0, "duration_seconds": 1.2},
    "volume_spikes": [
        {
            "symbol": "SPKUSDT", "date": "2026-08-10", "ratio": 8.0,
            "volume": 8000.0, "quote_volume": 900000.0, "close": 116.0,
            "watch_until": "2026-08-20",
        }
    ],
    "choch": {
        "latest_scan_at": "2026-08-11T04:00:00+00:00",
        "new_signal_count": 1,
        "signals": [
            {
                "symbol": "FLATUSDT", "tag": "BOS", "direction": "bullish",
                "layer": "internal", "signal_time": "2026-08-11T04:00:00+00:00",
                "structure_time": "2026-08-10T20:00:00+00:00",
                "close": 112.0, "level": 110.0, "breakout_pct": 1.82,
                "key": "FLATUSDT:internal:BOS:123", "trace": [101.0, 102.0, 103.0, 112.0],
            }
        ],
        "history": [
            {
                "symbol": "MAVUSDT", "tag": "CHoCH", "direction": "bearish",
                "layer": "swing", "signal_time": "2026-08-11T03:59:59.999000+00:00",
                "level": 0.052, "close": 0.051, "breakout_pct": -1.92,
            }
        ],
    },
}


def test_build_site_writes_expected_files(tmp_path):
    output = tmp_path / "public"
    build_site(output, LATEST)
    assert (output / "index.html").is_file()
    assert (output / "status.json").is_file()
    latest = json.loads((output / "data" / "latest.json").read_text())
    assert latest["schema_version"] == 2
    status = json.loads((output / "status.json").read_text())
    assert status["generated_at"] == LATEST["generated_at"]
    assert status["status"] == "ok"


def test_page_contains_data_and_visual_tokens(tmp_path):
    output = tmp_path / "public"
    build_site(output, LATEST)
    html = (output / "index.html").read_text()
    assert "SPKUSDT" in html and "FLATUSDT" in html
    assert "#0b0e11" in html and "#fcd535" in html and "#1e2329" in html
    assert "spark-pivot" in html and "tabular-nums" in html
    assert "data-tz='2026-08-11T04:00:00+00:00'" in html
    assert "data-ratio='8.0'" in html
    assert "data-sort='ratio' data-type='n'" in html
    assert "data-time='2026-08-11T03:59:59.999000+00:00'" in html
    assert "function sortTable" in html


def test_atomic_swap_keeps_previous_site_on_failure(tmp_path, monkeypatch):
    output = tmp_path / "public"
    build_site(output, LATEST)
    first_html = (output / "index.html").read_text()


    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(OSError):
        build_site(output, {**LATEST, "generated_at": "2026-08-12T00:00:00+00:00"})
    monkeypatch.undo()
    assert (output / "index.html").read_text() == first_html
    assert not (tmp_path / "public.staging").exists()


def test_subscription_form_absent_without_site_key(tmp_path):
    output = tmp_path / "public"
    build_site(output, LATEST)
    assert "subscribe-form" not in (output / "index.html").read_text()


def test_subscription_form_present_with_site_key(tmp_path):
    output = tmp_path / "public"
    build_site(output, LATEST, site_key="0xTESTKEY")
    html = (output / "index.html").read_text()
    assert "subscribe-form" in html
    assert "0xTESTKEY" in html
    assert "challenges.cloudflare.com/turnstile/v0/api.js" in html


def test_escaping_of_malicious_symbol(tmp_path):
    latest = json.loads(json.dumps(LATEST))
    latest["volume_spikes"][0]["symbol"] = '<img src=x onerror=alert(1)>USDT'
    output = tmp_path / "public"
    build_site(output, latest)
    html = (output / "index.html").read_text()
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_chart_card_tokens_present(tmp_path):
    output = tmp_path / "public"
    build_site(output, LATEST)
    html = (output / "index.html").read_text()
    assert "data-chart='1'" in html
    assert "id='spike-table'" in html
    assert "initChartCard" in html
    assert "chart-modal" in html and "chart-card" in html and "chart-svg" in html
    assert "chart-tab" in html
    assert 'fetch("charts/" + symbol + ".json")' in html


def test_charts_copied_into_site(tmp_path):
    charts_dir = tmp_path / "state" / "charts"
    charts_dir.mkdir(parents=True)
    (charts_dir / "SPKUSDT.json").write_text(json.dumps({"symbol": "SPKUSDT", "candles": []}))
    (charts_dir / "note.txt").write_text("ignored")
    output = tmp_path / "public"
    build_site(output, LATEST, charts_dir=charts_dir)
    assert (output / "charts" / "SPKUSDT.json").is_file()
    payload = json.loads((output / "charts" / "SPKUSDT.json").read_text())
    assert payload["symbol"] == "SPKUSDT"
    assert not (output / "charts" / "note.txt").exists()


def test_charts_dir_absent_when_not_provided(tmp_path):
    output = tmp_path / "public"
    build_site(output, LATEST)
    assert not (output / "charts").exists()
