import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import FakeClient
from shanzhai.review import (
    build_prompt,
    compose_reviews,
    evaluate_pending,
    llm_config,
    measure,
    pending_signals,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def matured_signal(now=NOW, hours_ago=25, symbol="AAAUSDT") -> dict:
    signal_time = (now - timedelta(hours=hours_ago)).isoformat()
    return {
        "symbol": symbol, "tag": "BOS", "direction": "bullish", "layer": "swing",
        "signal_time": signal_time, "close": 100.0, "level": 110.0,
        "key": f"{symbol}:swing:BOS:1234567890",
    }


def seed_history(state_dir, signals):
    path = state_dir / "choch_history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(signals))


class TestPendingSignals:
    def test_only_matured_unreviewed(self, tmp_path):
        seed_history(tmp_path, [matured_signal(hours_ago=25), matured_signal(hours_ago=23)])
        pending = pending_signals(tmp_path, NOW, json.loads((tmp_path / "choch_history.json").read_text()))
        assert len(pending) == 1
        assert pending[0]["key"].endswith("1234567890")

    def test_reviewed_keys_are_excluded(self, tmp_path):
        seed_history(tmp_path, [matured_signal()])
        (tmp_path / "reviews.json").write_text(json.dumps([{"key": "AAAUSDT:swing:BOS:1234567890"}]))
        history = json.loads((tmp_path / "choch_history.json").read_text())
        assert pending_signals(tmp_path, NOW, history) == []

    def test_batch_capped(self, tmp_path):
        signals = [matured_signal(hours_ago=30 + i) for i in range(15)]
        seed_history(tmp_path, signals)
        history = json.loads((tmp_path / "choch_history.json").read_text())
        assert len(pending_signals(tmp_path, NOW, history)) == 10

    def test_internal_layer_excluded(self, tmp_path):
        internal = matured_signal(hours_ago=30)
        internal["layer"] = "internal"
        internal["key"] = "AAAUSDT:internal:BOS:1234567890"
        seed_history(tmp_path, [matured_signal(hours_ago=30), internal])
        history = json.loads((tmp_path / "choch_history.json").read_text())
        pending = pending_signals(tmp_path, NOW, history)
        assert len(pending) == 1
        assert pending[0]["layer"] == "swing"

    def test_stale_signals_older_than_48h_excluded(self, tmp_path):
        seed_history(tmp_path, [matured_signal(hours_ago=100)])
        history = json.loads((tmp_path / "choch_history.json").read_text())
        assert pending_signals(tmp_path, NOW, history) == []

    def test_not_yet_matured_excluded(self, tmp_path):
        seed_history(tmp_path, [matured_signal(hours_ago=10)])
        history = json.loads((tmp_path / "choch_history.json").read_text())
        assert pending_signals(tmp_path, NOW, history) == []


class TestMeasure:
    def test_computes_window_stats(self):
        client = FakeClient(NOW, breakouts={"AAAUSDT"})
        result = measure(client, matured_signal(hours_ago=24), NOW)
        assert result["close_24h"] == 112.0
        assert result["change_pct"] == pytest.approx(12.0)
        assert result["high_pct"] == pytest.approx(15.0)
        assert result["low_pct"] == pytest.approx(-20.0)


class TestEvaluatePending:
    def test_writes_verdicts_idempotently(self, tmp_path):
        seed_history(tmp_path, [matured_signal()])
        client = FakeClient(NOW, breakouts={"AAAUSDT"})
        def llm(prompt):
            return json.dumps({"reviews": [{"key": "AAAUSDT:swing:BOS:1234567890", "verdict": "hit", "confidence": 82, "reason": "突破后持续走强，方向明确"}]})
        first = evaluate_pending(client, tmp_path, NOW, llm)
        assert first["evaluated"] == 1
        reviews = json.loads((tmp_path / "reviews.json").read_text())
        assert reviews[0]["verdict"] == "hit"
        assert reviews[0]["change_pct"] == 12.0
        assert reviews[0]["confidence"] == 82
        second = evaluate_pending(client, tmp_path, NOW, llm)
        assert second["evaluated"] == 0

    def test_llm_failure_leaves_pending(self, tmp_path):
        seed_history(tmp_path, [matured_signal()])
        client = FakeClient(NOW, breakouts={"AAAUSDT"})
        result = evaluate_pending(client, tmp_path, NOW, lambda prompt: (_ for _ in ()).throw(RuntimeError("boom")))
        assert result["llm_failures"] == 1
        assert not (tmp_path / "reviews.json").exists()

    def test_bad_verdict_skipped(self, tmp_path):
        seed_history(tmp_path, [matured_signal()])
        client = FakeClient(NOW, breakouts={"AAAUSDT"})
        def llm(prompt):
            return json.dumps({"reviews": [{"key": "AAAUSDT:swing:BOS:1234567890", "verdict": "weird", "reason": "x"}]})
        result = evaluate_pending(client, tmp_path, NOW, llm)
        assert result["evaluated"] == 0
        assert not (tmp_path / "reviews.json").exists()


class TestComposeReviews:
    def test_summary_and_rate(self, tmp_path):
        (tmp_path / "reviews.json").write_text(
            json.dumps([
                {"key": "a", "verdict": "hit", "evaluated_at": "2026-08-13T10:00:00+00:00"},
                {"key": "b", "verdict": "hit", "evaluated_at": "2026-08-13T11:00:00+00:00"},
                {"key": "c", "verdict": "partial", "evaluated_at": "2026-08-13T12:00:00+00:00"},
                {"key": "d", "verdict": "miss", "evaluated_at": "2026-08-13T09:00:00+00:00"},
            ])
        )
        summary = compose_reviews(tmp_path)
        assert summary["evaluated_count"] == 4
        assert summary["hit_count"] == 2
        assert summary["hit_rate"] == pytest.approx(0.5)
        assert summary["recent"][0]["key"] == "c"

    def test_empty(self, tmp_path):
        summary = compose_reviews(tmp_path)
        assert summary["evaluated_count"] == 0
        assert summary["hit_rate"] is None
        assert summary["recent"] == []

    def test_ai_configured_flag(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AI_API_KEY", raising=False)
        assert compose_reviews(tmp_path)["ai_configured"] is False
        monkeypatch.setenv("AI_API_KEY", "sk-test")
        assert compose_reviews(tmp_path)["ai_configured"] is True


class TestPromptAndConfig:
    def test_build_prompt_contains_context(self):
        prompt = build_prompt([matured_signal()])
        assert "AAAUSDT" in prompt
        assert "24h涨跌幅" in prompt

    def test_llm_config_requires_key(self, monkeypatch):
        monkeypatch.delenv("AI_API_KEY", raising=False)
        assert llm_config() is None
        monkeypatch.setenv("AI_API_KEY", "sk-1")
        config = llm_config()
        assert config is not None
        assert config["model"] == "deepseek-chat"
        assert config["base_url"] == "https://api.deepseek.com"
