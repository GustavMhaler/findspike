import json
from datetime import datetime, timedelta, timezone

from conftest import FakeClient
from shanzhai.charts import chart_path, fetch_chart, spike_symbols, update_charts

UTC = timezone.utc
NOW = datetime(2026, 8, 11, 4, 50, tzinfo=UTC)


class TestFetchChart:
    def test_payload_shape_closed_only(self):
        client = FakeClient(NOW, breakouts=set())
        payload = fetch_chart(client, "AAAUSDT", NOW)
        assert payload["symbol"] == "AAAUSDT"
        assert payload["interval"] == "1d"
        assert payload["updated_at"] == NOW.isoformat()
        candles = payload["candles"]
        assert 1 <= len(candles) <= 90
        for candle in candles:
            assert set(candle) == {"t", "o", "h", "l", "c", "v", "q"}
            assert candle["h"] >= max(candle["o"], candle["c"])
            assert candle["l"] <= min(candle["o"], candle["c"])
        # newest closed daily candle opens exactly one day before `now`'s date
        assert datetime.fromtimestamp(candles[-1]["t"] / 1000, UTC).date() == (NOW - timedelta(days=1)).date()


class TestUpdateCharts:
    def test_writes_payloads_per_symbol(self, tmp_path):
        client = FakeClient(NOW, breakouts=set())
        summary = update_charts(client, ["AAAUSDT", "BBBUSDT"], tmp_path, NOW)
        assert summary["succeeded"] == 2
        assert summary["failures"] == []
        payload = json.loads(chart_path(tmp_path, "AAAUSDT").read_text())
        assert payload["symbol"] == "AAAUSDT"
        assert payload["candles"]

    def test_per_symbol_failure_is_collected_not_raised(self, tmp_path):
        client = FakeClient(NOW, breakouts=set(), fail_symbols={"BBBUSDT"})
        summary = update_charts(client, ["AAAUSDT", "BBBUSDT"], tmp_path, NOW)
        assert summary["succeeded"] == 1
        assert len(summary["failures"]) == 1
        assert summary["failures"][0]["symbol"] == "BBBUSDT"
        assert chart_path(tmp_path, "AAAUSDT").is_file()
        assert not chart_path(tmp_path, "BBBUSDT").exists()

    def test_empty_symbol_list(self, tmp_path):
        summary = update_charts(FakeClient(NOW, breakouts=set()), [], tmp_path, NOW)
        assert summary["succeeded"] == 0
        assert summary["symbols"] == 0


class TestSpikeSymbols:
    def test_unique_in_table_order(self):
        daily = {"spikes": [{"symbol": "BBBUSDT"}, {"symbol": "AAAUSDT"}, {"symbol": "BBBUSDT"}]}
        assert spike_symbols(daily) == ["BBBUSDT", "AAAUSDT"]

    def test_missing_or_empty(self):
        assert spike_symbols({}) == []
        assert spike_symbols({"spikes": []}) == []
