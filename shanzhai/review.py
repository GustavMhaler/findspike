"""AI review of CHoCH signals: 24 hours after a signal, measure the move and
ask the LLM to judge accuracy (hit / partial / miss).

Config comes from the environment (see deploy/shanzhai.env.example):
    AI_BASE_URL  OpenAI-compatible endpoint, default https://api.deepseek.com
    AI_API_KEY   API key; when empty the review run is a no-op
    AI_MODEL     model name, default deepseek-chat

State: state/reviews.json, one entry per signal key, idempotent. LLM failures
leave the signal pending so the next run retries it.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .binance_api import BinancePublicClient
from .io_utils import read_json, write_json

REVIEW_WINDOW = timedelta(hours=24)
MAX_PER_BATCH = 10
# Reviews cover swing-layer signals only (same as page/email).
REVIEWED_LAYERS = ("swing",)

ENV_BASE_URL = "AI_BASE_URL"
ENV_API_KEY = "AI_API_KEY"
ENV_MODEL = "AI_MODEL"


def llm_config() -> dict | None:
    key = os.environ.get(ENV_API_KEY, "").strip()
    if not key:
        return None
    return {
        "base_url": os.environ.get(ENV_BASE_URL, "https://api.deepseek.com").rstrip("/"),
        "api_key": key,
        "model": os.environ.get(ENV_MODEL, "deepseek-chat").strip(),
    }


def call_llm(config: dict, prompt: str, timeout: float = 40) -> str:
    """OpenAI-compatible chat completion; returns the raw assistant content."""
    body = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": "你是严谨的加密货币市场信号复盘助手，只用中文回答，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{config['base_url']}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
            "User-Agent": "shanzhai-signal-desk/0.1",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.load(response)
    return payload["choices"][0]["message"]["content"]


def _parse_llm_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def pending_signals(state_dir: Path, now: datetime, history: list[dict]) -> list[dict]:
    reviewed_keys = {entry["key"] for entry in read_json(state_dir / "reviews.json", [])}
    cutoff = now - REVIEW_WINDOW  # mature: at least 24h old
    pending = [
        item for item in history
        if item.get("layer") in REVIEWED_LAYERS
        and item["key"] not in reviewed_keys
        and datetime.fromisoformat(item["signal_time"]) <= cutoff
    ]
    pending.sort(key=lambda item: item["signal_time"])
    return pending[:MAX_PER_BATCH]


def measure(client: BinancePublicClient, signal: dict, now: datetime) -> dict:
    """Fetch the 24h window after the signal and compute the move stats."""
    signal_time = datetime.fromisoformat(signal["signal_time"])
    end = min(signal_time + REVIEW_WINDOW, now)
    candles = client.candles(
        signal["symbol"], "1h", 30, start_time=signal_time + timedelta(hours=1), end_time=end
    )
    base = float(signal["close"])
    if not candles:
        raise RuntimeError("no candles in review window")
    close_24h = candles[-1].close
    high_24h = max(c.close_time <= end and c.high for c in candles)
    low_24h = min(c.close_time <= end and c.low for c in candles)
    return {
        "close_24h": round(close_24h, 8),
        "change_pct": round((close_24h / base - 1) * 100, 2),
        "high_pct": round((high_24h / base - 1) * 100, 2),
        "low_pct": round((low_24h / base - 1) * 100, 2),
    }


def build_prompt(pending: list[dict]) -> str:
    lines = [
        "以下是收盘价突破已确认结构位触发的 BOS/CHoCH 信号及其 24 小时后的表现（1 小时 K 线）。",
        "判定口径：上涨信号(bullish)24h 后上涨视为准确，下跌信号(bearish)24h 后下跌视为准确；",
        "涨幅可观、方向持续 = hit；方向对但力度弱或冲高回落 = partial；方向相反 = miss。",
        "请为每条信号给出 verdict(hit/partial/miss)、confidence(0-100)、一句话中文 reason（约 40 字）。",
        "只返回 JSON：{\"reviews\":[{\"key\":\"...\",\"verdict\":\"...\",\"confidence\":0,\"reason\":\"...\"}]}",
        "",
    ]
    for item in pending:
        lines.append(
            f"- key={item['key']} {item['symbol']} {item['direction']} {item['tag']} "
            f"信号收盘价={item['close']} 24h涨跌幅={item.get('change_pct')}% "
            f"24h最高={item.get('high_pct')}% 24h最低={item.get('low_pct')}%"
        )
    return "\n".join(lines)


def evaluate_pending(
    client: BinancePublicClient,
    state_dir: Path,
    now: datetime,
    llm: Callable[[str], str],
    history: list[dict] | None = None,
) -> dict:
    """Evaluate matured, unreviewed signals; returns a summary dict."""
    history_list = history if history is not None else read_json(state_dir / "choch_history.json", [])
    pending = pending_signals(state_dir, now, history_list)
    if not pending:
        return {"evaluated": 0, "llm_failures": 0, "fetch_failures": 0}

    enriched = []
    fetch_failures = 0
    for item in pending:
        try:
            enriched.append({**item, **measure(client, item, now)})
        except Exception:
            fetch_failures += 1
    enriched = [e for e in enriched if "change_pct" in e]
    if not enriched:
        return {"evaluated": 0, "llm_failures": 0, "fetch_failures": fetch_failures}

    try:
        raw = llm(build_prompt(enriched))
        verdicts = _parse_llm_json(raw).get("reviews", [])
    except Exception:
        return {"evaluated": 0, "llm_failures": len(enriched), "fetch_failures": fetch_failures}

    by_key = {v.get("key"): v for v in verdicts}
    reviews = read_json(state_dir / "reviews.json", [])
    evaluated = 0
    for item in enriched:
        verdict = by_key.get(item["key"])
        if not verdict or verdict.get("verdict") not in ("hit", "partial", "miss"):
            continue
        reviews.append(
            {
                "key": item["key"], "symbol": item["symbol"], "direction": item["direction"],
                "tag": item["tag"], "signal_time": item["signal_time"],
                "close_at_signal": item["close"], "close_24h": item["close_24h"],
                "change_pct": item["change_pct"], "high_pct": item["high_pct"],
                "low_pct": item["low_pct"],
                "verdict": verdict["verdict"],
                "confidence": int(verdict.get("confidence", 0)),
                "reason": str(verdict.get("reason", ""))[:200],
                "evaluated_at": now.isoformat(),
            }
        )
        evaluated += 1
    if evaluated:
        write_json(state_dir / "reviews.json", reviews)
    return {"evaluated": evaluated, "llm_failures": 0, "fetch_failures": fetch_failures}


def compose_reviews(state_dir: Path) -> dict:
    reviews = read_json(state_dir / "reviews.json", [])
    total = len(reviews)
    hits = sum(1 for r in reviews if r["verdict"] == "hit")
    partials = sum(1 for r in reviews if r["verdict"] == "partial")
    misses = total - hits - partials
    recent = sorted(reviews, key=lambda r: r["evaluated_at"], reverse=True)[:20]
    return {
        "evaluated_count": total,
        "hit_count": hits,
        "partial_count": partials,
        "miss_count": misses,
        "hit_rate": round(hits / total, 3) if total else None,
        "ai_configured": llm_config() is not None,
        "recent": recent,
    }
