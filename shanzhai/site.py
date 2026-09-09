"""Static site builder.

Renders the dashboard from the latest data contract into a staging directory
and swaps it in atomically. Any failure before the swap leaves the previous
`public/` untouched, so the last known good site survives.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

TIMEZONE = "Asia/Shanghai"

CSS = """
:root {
  --canvas: #0b0e11;
  --surface: #1e2329;
  --surface-2: #2b3139;
  --hairline: #2b3139;
  --primary: #fcd535;
  --primary-active: #f0b90b;
  --on-primary: #181a20;
  --up: #0ecb81;
  --down: #f6465d;
  --body: #eaecef;
  --muted: #707a8a;
  --muted-2: #929aa5;
  --focus: rgba(59, 130, 246, 0.5);
  --footer-bg: #fafafa;
  --footer-ink: #181a20;
  --font-body: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-num: "IBM Plex Sans", "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
html { color-scheme: dark; }
body {
  margin: 0;
  background: var(--canvas);
  color: var(--body);
  font-family: var(--font-body);
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
.num { font-family: var(--font-num); font-variant-numeric: tabular-nums; }
.visually-hidden {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}
a { color: var(--primary); text-decoration: none; }
a:hover { color: var(--primary-active); }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }

.container { max-width: 1280px; margin: 0 auto; padding: 0 24px; }

.topbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; padding: 20px 0; border-bottom: 1px solid var(--hairline);
  flex-wrap: wrap;
}
.brand { display: flex; align-items: center; gap: 10px; font-size: 20px; font-weight: 600; color: #fff; }
.brand-mark { width: 12px; height: 12px; border-radius: 3px; background: var(--primary); flex: none; }
.topmeta { display: flex; align-items: center; gap: 18px; font-size: 13px; color: var(--muted); flex-wrap: wrap; }
.status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--up); margin-right: 6px; vertical-align: 1px; }
.status-dot.stale { background: var(--primary); }
.status-dot.dead { background: var(--down); }

.banner {
  display: none; margin: 16px 0 0; padding: 12px 16px; border: 1px solid var(--hairline);
  border-left: 3px solid var(--primary); background: var(--surface); border-radius: 8px;
  font-size: 13px; color: var(--body);
}
.banner.visible { display: block; }

.cols { display: grid; grid-template-columns: 8fr 4fr; gap: 24px; margin: 24px 0; align-items: start; }
.col { display: flex; flex-direction: column; gap: 24px; min-width: 0; }
.card {
  background: var(--surface); border: 1px solid var(--hairline); border-radius: 12px;
  padding: 24px; overflow: hidden;
}
.card h2 { margin: 0 0 4px; font-size: 16px; font-weight: 600; color: #fff; }
.card .sub { margin: 0 0 16px; font-size: 12px; color: var(--muted); }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th {
  text-align: left; font-weight: 500; font-size: 12px; color: var(--muted);
  padding: 8px 12px; border-bottom: 1px solid var(--hairline); white-space: nowrap;
}
th[data-sort] button {
  background: none; border: none; padding: 0; margin: 0;
  font: inherit; color: inherit; cursor: pointer; white-space: nowrap;
}
th[data-sort] button:hover { color: var(--body); }
th[data-sort] button:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; border-radius: 2px; }
th.sort-active { color: var(--body); }
th.sort-active.sort-asc::after { content: " ▲"; color: var(--primary); }
th.sort-active.sort-desc::after { content: " ▼"; color: var(--primary); }
td { padding: 12px; border-bottom: 1px solid var(--hairline); white-space: nowrap; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: var(--surface-2); }
td.ratio { color: var(--primary); font-weight: 600; }
td.up { color: var(--up); }
td.down { color: var(--down); }
.symbol { font-weight: 600; color: #fff; }
.scroll { overflow-x: auto; margin: 0 -24px; padding: 0 24px; }
.scroll-v { max-height: 600px; overflow-y: auto; }
.scroll-v thead th { position: sticky; top: 0; background: var(--surface); z-index: 1; }
.empty { padding: 24px 0; color: var(--muted); font-size: 13px; }

.signal { padding: 16px 0; border-bottom: 1px solid var(--hairline); }
.signal:first-of-type { padding-top: 0; }
.signal:last-of-type { border-bottom: none; }
.signal-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.signal-symbol { font-weight: 600; color: #fff; font-size: 15px; }
.tag {
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  font-size: 12px; font-weight: 600; line-height: 1.4;
}
.tag-bull { color: var(--up); border: 1px solid var(--up); }
.tag-bear { color: var(--down); border: 1px solid var(--down); }
.tag-mail { color: var(--primary); border: 1px solid var(--primary); }
.tag-site { color: var(--muted); border: 1px solid var(--hairline); }
.tag-level-small { color: #a6c8ff; border: 1px solid #a6c8ff; }
.tag-level-major { color: #ffd166; border: 1px solid #ffd166; }
.signal-time { font-size: 12px; color: var(--muted); }
.signal-body { display: flex; gap: 20px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
.signal-fig { min-width: 150px; }
.signal-fig .fig-row { display: flex; justify-content: space-between; gap: 16px; font-size: 13px; padding: 3px 0; }
.signal-fig .fig-label { color: var(--muted); font-size: 12px; }
.spark { display: block; width: 240px; max-width: 100%; height: 48px; margin-top: 8px; }
.spark-line { fill: none; stroke: var(--muted-2); stroke-width: 1; }
.spark-pivot { stroke: var(--primary); stroke-width: 1; stroke-dasharray: 3 3; }
.spark-last { fill: var(--primary); }
.anti-repaint { margin-top: 12px; font-size: 12px; color: var(--muted); }

tr[data-chart] { cursor: pointer; }
.chart-modal {
  display: none; position: fixed; inset: 0; z-index: 60;
  background: rgba(0, 0, 0, 0.55); padding: 24px;
}
.chart-modal.visible { display: flex; align-items: center; justify-content: center; }
.chart-card {
  width: min(860px, 100%); max-height: 94vh; overflow: auto;
  background: var(--surface); border: 1px solid var(--hairline); border-radius: 14px;
  padding: 18px 20px 14px; box-shadow: 0 18px 50px rgba(0, 0, 0, 0.5);
}
.chart-head { display: flex; align-items: center; gap: 12px; }
.chart-symbol { font-weight: 600; color: #fff; font-size: 16px; }
.chart-tabs { display: flex; gap: 6px; margin-left: auto; }
.chart-tab {
  border: 1px solid var(--hairline); background: transparent; color: var(--muted);
  font: inherit; font-size: 12px; padding: 4px 12px; border-radius: 999px; cursor: pointer;
}
.chart-tab:hover { color: var(--body); }
.chart-tab.active {
  background: var(--primary); border-color: var(--primary);
  color: var(--on-primary); font-weight: 600;
}
.chart-x {
  border: none; background: transparent; color: var(--muted);
  font-size: 16px; line-height: 1; cursor: pointer; padding: 4px 6px;
}
.chart-x:hover { color: #fff; }
.chart-sub { margin: 8px 0 4px; font-size: 12px; color: var(--muted); }
.chart-sub .up { color: var(--up); }
.chart-sub .down { color: var(--down); }
.chart-body { margin-top: 4px; }
.chart-svg { display: block; width: 100%; height: auto; }
.chart-svg .grid { stroke: var(--hairline); }
.chart-svg .cu { stroke: var(--up); fill: var(--up); }
.chart-svg .cd { stroke: var(--down); fill: var(--down); }
.chart-svg .cv-up { fill: var(--up); opacity: 0.3; }
.chart-svg .cv-down { fill: var(--down); opacity: 0.3; }
.chart-svg .cv-spike { fill: var(--primary); opacity: 0.85; }
.chart-svg .spike-line { stroke: var(--primary); stroke-dasharray: 4 4; opacity: 0.8; }
.chart-svg .axis { fill: var(--muted); font-size: 12px; font-family: var(--font-num); }

.review-summary {
  display: flex; flex-wrap: wrap; gap: 12px; align-items: baseline;
  padding: 10px 0 14px; border-bottom: 1px solid var(--hairline);
  font-size: 13px; margin-bottom: 6px;
}
.review-count { font-weight: 600; color: #fff; }
.review-rate { color: var(--primary); font-weight: 600; }
.review-hits { color: var(--up); }
.review-partials { color: var(--primary-active); }
.review-misses { color: var(--down); }
.review-row { padding: 10px 0; border-bottom: 1px solid var(--hairline); }
.review-row:last-of-type { border-bottom: none; }
.review-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; }
.review-tag { font-size: 11px; color: var(--muted-2); border: 1px solid var(--hairline); border-radius: 3px; padding: 1px 5px; }
.review-time { font-size: 11px; color: var(--muted); margin-left: auto; }
.verdict { font-size: 12px; font-weight: 600; padding: 1px 7px; border-radius: 4px; }
.verdict.hit { color: var(--up); border: 1px solid var(--up); }
.verdict.partial { color: var(--primary); border: 1px solid var(--primary); }
.verdict.miss { color: var(--down); border: 1px solid var(--down); }
.review-reason { margin-top: 4px; font-size: 12px; color: var(--muted-2); }

.subscribe { margin: 24px 0; }
.subscribe form { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
.subscribe input[type="email"] {
  min-height: 44px; width: 280px; max-width: 100%; padding: 10px 16px;
  background: var(--surface-2); border: 1px solid var(--hairline); border-radius: 8px;
  color: var(--body); font-size: 14px; font-family: var(--font-body);
}
.subscribe input[type="email"]:focus { outline: 2px solid var(--focus); outline-offset: 1px; }
.btn {
  min-height: 44px; display: inline-flex; align-items: center; justify-content: center;
  padding: 10px 24px; border: none; border-radius: 6px; cursor: pointer;
  background: var(--primary); color: var(--on-primary); font-size: 14px; font-weight: 600;
  font-family: var(--font-body);
}
.btn:hover { background: var(--primary-active); }
.btn:disabled { background: #3a3a1f; color: #707a8a; cursor: default; }
.sub-msg { margin-top: 12px; font-size: 13px; min-height: 20px; }
.sub-msg.error { color: var(--down); }
.sub-msg.ok { color: var(--up); }
.sub-note { margin-top: 8px; font-size: 12px; color: var(--muted); }

.footer {
  margin-top: 48px; background: var(--footer-bg); color: var(--footer-ink);
  padding: 48px 0 40px;
}
.footer .inner { max-width: 1280px; margin: 0 auto; padding: 0 24px; }
.footer-grid { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 32px; }
.footer h3 { font-size: 14px; font-weight: 600; margin: 0 0 12px; }
.footer ul { list-style: none; margin: 0; padding: 0; }
.footer li { margin: 6px 0; font-size: 13px; }
.footer a { color: var(--footer-ink); }
.footer a:hover { color: var(--primary-active); }
.footer .fine { margin-top: 32px; font-size: 12px; color: #707a8a; border-top: 1px solid #eaecef; padding-top: 16px; }

@media (max-width: 1024px) {
  .cols { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 767px) {
  .cols { grid-template-columns: 1fr; }
  .container { padding: 0 16px; }
  .card { padding: 16px; }
  .scroll { margin: 0 -16px; padding: 0 16px; }
  .topmeta { gap: 10px; }
  .footer-grid { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
}
"""


def _fmt_price(value: float) -> str:
    if value >= 1000:
        text = f"{value:,.2f}"
    elif value >= 1:
        text = f"{value:,.4f}"
    else:
        text = f"{value:.8f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _fmt_volume(value: float) -> str:
    if value >= 1e9:
        return f"{value / 1e9:.2f}B"
    if value >= 1e6:
        return f"{value / 1e6:.2f}M"
    if value >= 1e3:
        return f"{value / 1e3:.2f}K"
    return f"{value:.0f}"


def _fmt_ratio(value: float) -> str:
    return f"{value:.2f}x"


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _fmt_dt(iso: str) -> str:
    return f"<span class='num' data-tz='{html.escape(iso, quote=True)}'></span>"


def _spike_rows(spikes: list[dict]) -> str:
    if not spikes:
        return "<div class='empty'>No volume spikes in the last 10 closed daily candles.</div>"
    rows = []
    for index, spike in enumerate(spikes, 1):
        rows.append(
            "<tr"
            " data-chart='1'"
            f" data-idx='{index - 1}'"
            f" data-symbol='{html.escape(spike['symbol'], quote=True)}'"
            f" data-date='{html.escape(spike['date'], quote=True)}'"
            f" data-ratio='{spike['ratio']}'"
            f" data-volume='{spike['volume']}'"
            f" data-quote='{spike['quote_volume']}'"
            f" data-watch='{html.escape(spike['watch_until'], quote=True)}'>"
            f"<td class='num'>{index}</td>"
            f"<td class='symbol'>{html.escape(spike['symbol'])}</td>"
            f"<td class='num'>{html.escape(spike['date'])}</td>"
            f"<td class='ratio num'>{_fmt_ratio(spike['ratio'])}</td>"
            f"<td class='num'>{_fmt_volume(spike['volume'])}</td>"
            f"<td class='num'>{_fmt_volume(spike['quote_volume'])}</td>"
            f"<td class='num'>{html.escape(spike['watch_until'])}</td>"
            "</tr>"
        )
    return (
        "<div class='scroll scroll-v'><table id='spike-table'><thead><tr>"
        "<th scope='col'>#</th>"
        "<th scope='col' data-sort='symbol' data-type='s'><button type='button'>Symbol</button></th>"
        "<th scope='col' data-sort='date' data-type='s'><button type='button'>Spike Date</button></th>"
        "<th scope='col' data-sort='ratio' data-type='n' class='sort-active sort-desc' aria-sort='descending'><button type='button'>Ratio</button></th>"
        "<th scope='col' data-sort='volume' data-type='n'><button type='button'>Base Volume</button></th>"
        "<th scope='col' data-sort='quote' data-type='n'><button type='button'>Quote Volume</button></th>"
        "<th scope='col' data-sort='watch' data-type='s'><button type='button'>Watch Until</button></th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _sparkline(signal: dict) -> str:
    trace = signal.get("trace") or []
    pivot = signal["level"]
    width, height = 240, 48
    if len(trace) < 2:
        return ""
    lo = min(min(trace), pivot * 0.995)
    hi = max(max(trace), pivot * 1.005)
    if hi - lo < 1e-12:
        hi = lo + 1

    def x(index: int) -> float:
        return index / (len(trace) - 1) * width

    def y(value: float) -> float:
        return height - (value - lo) / (hi - lo) * (height - 6) - 3

    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(trace))
    rail = y(pivot)
    last_x, last_y = x(len(trace) - 1), y(trace[-1])
    return (
        f"<svg class='spark' viewBox='0 0 {width} {height}' aria-hidden='true'>"
        f"<polyline class='spark-line' points='{points}'/>"
        f"<line class='spark-pivot' x1='0' y1='{rail:.1f}' x2='{width}' y2='{rail:.1f}'/>"
        f"<circle class='spark-last' cx='{last_x:.1f}' cy='{last_y:.1f}' r='2.5'/>"
        "</svg>"
    )


def _tag_badge(signal: dict) -> str:
    tag = signal.get("tag", "BOS")
    direction = signal.get("direction", "bullish")
    css = "tag-bull" if direction == "bullish" else "tag-bear"
    return f"<span class='tag {css}'>{html.escape(tag)}</span>"


def _level_badge(signal: dict) -> str:
    level_tag = signal.get("level_tag", "first")
    if level_tag == "second":
        label, css = "二次突破", "tag-level-major"
    else:
        label, css = "首次突破", "tag-level-small"
    return f"<span class='tag {css}'>{html.escape(label)}</span>"


def _signal_cards(signals: list[dict]) -> str:
    if not signals:
        return "<div class='empty'>No BOS/CHoCH in the last scan. Structure levels are confirmed by the candles that follow them, so signals appear only after confirmation.</div>"
    cards = []
    for signal in signals:
        breakout = signal["breakout_pct"]
        direction = "up" if signal["direction"] == "bullish" else "down"
        tier = (
            "<span class='tag tag-mail'>Email</span>"
            if signal.get("email_ok")
            else "<span class='tag tag-site'>Site</span>"
        )
        cards.append(
            "<div class='signal'>"
            "<div class='signal-head'>"
            f"<span class='signal-symbol'>{html.escape(signal['symbol'])}</span>"
            f"{_tag_badge(signal)}"
            f"{_level_badge(signal)}"
            f"{tier}"
            f"<span class='signal-time'>{html.escape(signal.get('layer', ''))} · signal {_fmt_dt(signal['signal_time'])}</span>"
            "</div>"
            f"<div class='signal-body'>"
            "<div class='signal-fig'>"
            f"<div class='fig-row'><span class='fig-label'>Close</span><span class='num'>{_fmt_price(signal['close'])}</span></div>"
            f"<div class='fig-row'><span class='fig-label'>Structure level</span><span class='num'>{_fmt_price(signal['level'])}</span></div>"
            f"<div class='fig-row'><span class='fig-label'>Move</span><span class='num {direction}'>{_fmt_pct(breakout)}</span></div>"
            "</div>"
            f"{_sparkline(signal)}"
            "</div>"
            "</div>"
        )
    note = (
        "<p class='anti-repaint'>结构位在 1h 线上确认，15m 收盘突破后才触发，信号不会重绘。"
        "同一币种首次突破只在网页端更新；第二次突破（二次突破）才推送邮件。"
        "BOS 顺势延续，CHoCH 走势反转。研究输出，不构成投资建议。</p>"
    )
    return "".join(cards) + note


def _history_rows(history: list[dict]) -> str:
    if not history:
        return "<div class='empty'>No BOS/CHoCH in the last 30 days.</div>"
    rows = []
    for index, item in enumerate(history):
        breakout = item.get("breakout_pct", 0)
        direction = "up" if item.get("direction", "bullish") == "bullish" else "down"
        rows.append(
            "<tr"
            f" data-idx='{index}'"
            f" data-time='{html.escape(item['signal_time'], quote=True)}'"
            f" data-symbol='{html.escape(item['symbol'], quote=True)}'"
            f" data-tag='{html.escape(item.get('tag', ''), quote=True)}'"
            f" data-layer='{html.escape(item.get('layer', ''), quote=True)}'"
            f" data-level='{item['level']}'"
            f" data-close='{item['close']}'"
            f" data-move='{breakout}'>"
            f"<td class='num'>{_fmt_dt(item['signal_time'])}</td>"
            f"<td class='symbol'>{html.escape(item['symbol'])}</td>"
            f"<td>{_tag_badge(item)}</td>"
            f"<td class='num'>{html.escape(item.get('layer', ''))}</td>"
            f"<td class='num'>{_fmt_price(item['level'])}</td>"
            f"<td class='num'>{_fmt_price(item['close'])}</td>"
            f"<td class='num {direction}'>{_fmt_pct(breakout)}</td>"
            "</tr>"
        )
    return (
        "<div class='scroll scroll-v'><table><thead><tr>"
        "<th scope='col' data-sort='time' data-type='s' class='sort-active sort-desc' aria-sort='descending'><button type='button'>Signal Time</button></th>"
        "<th scope='col' data-sort='symbol' data-type='s'><button type='button'>Symbol</button></th>"
        "<th scope='col' data-sort='tag' data-type='s'><button type='button'>Tag</button></th>"
        "<th scope='col' data-sort='layer' data-type='s'><button type='button'>Layer</button></th>"
        "<th scope='col' data-sort='level' data-type='n'><button type='button'>Level</button></th>"
        "<th scope='col' data-sort='close' data-type='n'><button type='button'>Close</button></th>"
        "<th scope='col' data-sort='move' data-type='n'><button type='button'>Move</button></th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _reviews_card(reviews: dict) -> str:
    total = reviews.get("evaluated_count", 0)
    hits = reviews.get("hit_count", 0)
    partials = reviews.get("partial_count", 0)
    misses = reviews.get("miss_count", 0)
    rate = reviews.get("hit_rate")

    if not reviews.get("ai_configured", False):
        body = "<div class='empty'>AI 复盘未配置 — 请在部署环境中填写 AI_API_KEY（见 deploy/shanzhai.env.example）。</div>"
    elif total == 0:
        body = "<div class='empty'>暂无复盘数据 — 新信号触发满 24 小时后自动评估。</div>"
    else:
        summary = (
            f"<div class='review-summary'>"
            f"<span class='review-count'>已评估 {total}</span>"
            f"<span class='review-rate'>命中率 {rate:.1%}</span>"
            f"<span class='review-hits'>{hits} 命中</span>"
            f"<span class='review-partials'>{partials} 部分</span>"
            f"<span class='review-misses'>{misses} 未中</span>"
            "</div>"
        )
        rows = []
        for item in reviews.get("recent", [])[:10]:
            bullish = item["direction"] == "bullish"
            arrow = "▲" if bullish else "▼"
            color = "up" if bullish else "down"
            change = item["change_pct"]
            move = f"<span class='num {color}'>{'+' if change >= 0 else ''}{change:.2f}%</span>"
            badge = {
                "hit": "<span class='verdict hit'>✓ 命中</span>",
                "partial": "<span class='verdict partial'>~ 部分</span>",
                "miss": "<span class='verdict miss'>✗ 未中</span>",
            }.get(item.get("verdict", "miss"), "")
            rows.append(
                "<div class='review-row'>"
                f"<div class='review-top'>"
                f"<span class='symbol'>{html.escape(item['symbol'])}</span>"
                f"<span class='num {color}'>{arrow}</span>"
                f"<span class='review-tag'>{html.escape(item.get('tag', ''))}</span>"
                f"{move}"
                f"{badge}"
                f"<span class='review-time num'>{_fmt_dt(item['evaluated_at'])}</span>"
                "</div>"
                f"<div class='review-reason'>{html.escape(item.get('reason', ''))}</div>"
                "</div>"
            )
        body = summary + "".join(rows)
    return (
        "<section class='card'>"
        "<h2>AI 复盘</h2>"
        "<p class='sub'>信号触发 24 小时后的涨跌回测与 AI 准确度评估。</p>"
        f"{body}"
        "</section>"
    )


def _page_html(latest: dict, site_key: str) -> str:
    spikes = latest.get("volume_spikes", [])
    choch = latest.get("choch", {})
    signals = choch.get("signals", [])
    history = choch.get("history", [])
    generated = latest["generated_at"]
    through = latest.get("data_candle_through")
    turnstile = site_key or ""

    subscribe_block = ""
    if turnstile:
        subscribe_block = (
            "<form id='subscribe-form' novalidate>"
            "<label class='visually-hidden' for='email'>Email address</label>"
            "<input id='email' name='email' type='email' required autocomplete='email' "
            "placeholder='you@example.com'/>"
            f"<div class='cf-turnstile' data-sitekey='{html.escape(turnstile, quote=True)}' data-theme='dark'></div>"
            "<button class='btn' id='subscribe-btn' type='submit'>Notify me</button>"
            "</form>"
            "<div class='sub-msg' id='sub-msg' role='status' aria-live='polite'></div>"
            "<p class='sub-note'>Double opt-in with one-click unsubscribe. One digest is sent only when a new "
            "CHoCH signal is confirmed; you will not get a daily email.</p>"
        )
    else:
        subscribe_block = (
            "<p class='empty'>Subscription service is unavailable right now — please check back later.</p>"
        )

    subscribe_js = _subscribe_js(turnstile)
    turnstile_script = (
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>\n'
        if turnstile
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Shanzhai Signal Desk</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Crect width='12' height='12' rx='3' fill='%23fcd535'/%3E%3C/svg%3E">
<style>{CSS}</style>
{turnstile_script}</head>
<body>
<header class="container topbar">
  <div class="brand"><span class="brand-mark"></span>Shanzhai Signal Desk</div>
  <div class="topmeta">
    <span><span class="status-dot" id="status-dot"></span><span id="status-label">site updated {_fmt_dt(generated)}</span></span>
    <span>last closed candle {_fmt_dt(through or generated)}</span>
    <a href="data/latest.json">data/latest.json</a>
  </div>
</header>

<div class="container">
  <div class="banner" id="stale-banner" role="alert"></div>

  <main class="cols">
    <div class="col">
      <section class="card">
        <h2>Volume spikes</h2>
        <p class="sub">Newest closed daily candle within the last 10, base volume at least 5x its preceding 7-candle average. Click a row for the daily / hourly chart.</p>
        {_spike_rows(spikes)}
      </section>

      <section class="card">
        <h2>CHoCH history</h2>
        <p class="sub">Confirmed breakouts from the last 30 days.</p>
        {_history_rows(history)}
      </section>
    </div>

    <div class="col">
      <section class="card">
        <h2>CHoCH breakouts</h2>
        <p class="sub">Close crossing a confirmed structure level. Last scan {_fmt_dt(choch.get('latest_scan_at') or generated)}.</p>
        {_signal_cards(signals)}
      </section>

      {_reviews_card(latest.get("reviews", {}))}

      <section class="card subscribe">
        <h2>Email notifications</h2>
        <p class="sub">Get one digest when a new CHoCH signal is confirmed.</p>
        {subscribe_block}
      </section>
    </div>
  </main>
</div>

<footer class="footer">
  <div class="inner">
    <div class="footer-grid">
      <div>
        <h3>Shanzhai Signal Desk</h3>
        <p style="margin:0;font-size:13px">Volume-spike universe and 1-hour BOS/CHoCH breakouts from public Binance market data.</p>
      </div>
      <div>
        <h3>Data</h3>
        <ul>
          <li><a href="data/latest.json">latest.json</a></li>
          <li><a href="status.json">status.json</a></li>
        </ul>
      </div>
      <div>
        <h3>Research</h3>
        <ul>
          <li><a href="https://github.com/anomalyco/opencode" rel="nofollow">source notebook</a></li>
        </ul>
      </div>
    </div>
    <p class="fine">All data is generated deterministically from closed Binance candles. Signals can be delayed by design:
       a structure is confirmed only by the candles that follow it. Past performance does not guarantee future results.
       This project is not investment advice.</p>
  </div>
</footer>

<script>
(function () {{
  var TZ = "Asia/Shanghai";
  var formatter = new Intl.DateTimeFormat(undefined, {{
    timeZone: TZ, year: "numeric", month: "short", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  }});
  function renderTimes() {{
    var nodes = document.querySelectorAll("[data-tz]");
    for (var i = 0; i < nodes.length; i++) {{
      var iso = nodes[i].getAttribute("data-tz");
      var date = new Date(iso);
      nodes[i].textContent = isNaN(date) ? iso : formatter.format(date);
    }}
  }}
  function staleness() {{
    var generated = new Date({json.dumps(generated)});
    var through = new Date({json.dumps(through or generated)});
    var now = Date.now();
    var banners = [];
    if (now - generated.getTime() > 26 * 3600 * 1000) {{
      banners.push("The page is stale: the last successful site build was more than 26 hours ago.");
    }}
    if (now - through.getTime() > 5 * 3600 * 1000) {{
      banners.push("Market data is stale: the last fully closed 1h candle is older than 2 hours.");
    }}
    var dot = document.getElementById("status-dot");
    var label = document.getElementById("status-label");
    if (banners.length) {{
      document.getElementById("stale-banner").textContent = banners.join(" ");
      document.getElementById("stale-banner").className = "banner visible";
      dot.className = now - generated.getTime() > 26 * 3600 * 1000 ? "status-dot dead" : "status-dot stale";
      label.textContent = "site may be stale";
    }}
  }}

  {subscribe_js}

  {_chart_js()}

  function sortTable(th) {{
    var table = th.closest("table");
    var tbody = table.tBodies[0];
    var key = th.getAttribute("data-sort");
    var type = th.getAttribute("data-type");
    var state = table.__sort || {{ key: null, dir: null }};
    if (state.key !== key) {{
      state = {{ key: key, dir: "asc" }};
    }} else if (state.dir === "asc") {{
      state = {{ key: key, dir: "desc" }};
    }} else {{
      state = {{ key: null, dir: null }};
    }}
    table.__sort = state;
    var headers = table.querySelectorAll("th[data-sort]");
    for (var j = 0; j < headers.length; j++) {{
      headers[j].removeAttribute("aria-sort");
      headers[j].classList.remove("sort-active", "sort-asc", "sort-desc");
    }}
    var rows = Array.prototype.slice.call(tbody.rows);
    if (state.key) {{
      var dir = state.dir === "desc" ? -1 : 1;
      rows.sort(function (a, b) {{
        var av = a.getAttribute("data-" + key);
        var bv = b.getAttribute("data-" + key);
        var cmp = type === "n" ? (parseFloat(av) - parseFloat(bv)) : av.localeCompare(bv);
        return cmp * dir;
      }});
      th.setAttribute("aria-sort", state.dir === "asc" ? "ascending" : "descending");
      th.classList.add("sort-active", "sort-" + state.dir);
    }} else {{
      rows.sort(function (a, b) {{
        return parseInt(a.getAttribute("data-idx"), 10) - parseInt(b.getAttribute("data-idx"), 10);
      }});
    }}
    for (var k = 0; k < rows.length; k++) tbody.appendChild(rows[k]);
  }}

  function initSort() {{
    var headers = document.querySelectorAll("th[data-sort]");
    for (var i = 0; i < headers.length; i++) {{
      (function (th) {{
        th.querySelector("button").addEventListener("click", function () {{ sortTable(th); }});
      }})(headers[i]);
    }}
  }}

  renderTimes();
  staleness();
  initSort();
  initChartCard();
}})();
</script>
</body>
</html>"""


def _chart_js() -> str:
    return r"""
  var CHART_W = 760, CHART_H = 380, CHART_PAD_L = 8, CHART_PAD_R = 62;
  var CHART_SETS = { daily: "1d", hourly: "1h" };
  var modal = document.createElement("div");
  modal.className = "chart-modal";
  modal.innerHTML = "<div class='chart-card' role='dialog' aria-modal='true'></div>";
  document.body.appendChild(modal);
  var card = modal.firstChild;
  var cache = {};
  var inflight = {};
  var current = null;
  var currentTab = "daily";
  var spikeDate = null;
  var lastFocus = null;

  function fmtPx(v) {
    if (v >= 1000) return v.toFixed(0);
    if (v >= 1) return v.toFixed(2).replace(/\.?0+$/, "");
    return v.toPrecision(3);
  }
  function fmtVol(v) {
    if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
    if (v >= 1e3) return (v / 1e3).toFixed(2) + "K";
    return String(Math.round(v));
  }
  function fmtDay(ts) {
    var d = new Date(ts);
    return (d.getMonth() + 1) + "/" + d.getDate();
  }
  function fmtHour(ts) {
    var d = new Date(ts);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " + d.getHours() + ":00";
  }

  function isSpikeDay(candle) {
    if (!spikeDate) return false;
    return new Date(candle.t).toISOString().slice(0, 10) === spikeDate;
  }

  function renderChart(symbol, payload) {
    var set = payload.intervals && payload.intervals[CHART_SETS[currentTab]];
    var candles = (set && set.candles) || [];
    if (!candles.length) {
      card.innerHTML = "<div class='chart-head'><span class='chart-symbol'>" + symbol + "</span><button class='chart-x' type='button' aria-label='Close'>&times;</button></div><div class='chart-sub'>no candles for this interval</div>";
      return;
    }
    var highs = [], lows = [];
    var i, c;
    for (i = 0; i < candles.length; i++) { highs.push(candles[i].h); lows.push(candles[i].l); }
    var hi = Math.max.apply(null, highs), lo = Math.min.apply(null, lows);
    if (!isFinite(hi) || !isFinite(lo) || hi <= lo) return;
    var pad = (hi - lo) * 0.06;
    hi += pad; lo -= pad;
    var plotW = CHART_W - CHART_PAD_R - CHART_PAD_L;
    var volH = 60, priceH = CHART_H - 18 - volH;
    var step = plotW / candles.length, bw = Math.max(1, step * 0.62);
    function py(v) { return (hi - v) / (hi - lo) * priceH + 4; }
    var maxVol = 0;
    for (i = 0; i < candles.length; i++) { if (candles[i].v > maxVol) maxVol = candles[i].v; }
    var last = candles[candles.length - 1];
    var chg = (last.c - candles[0].c) / candles[0].c * 100;
    var svg = ["<svg class='chart-svg' viewBox='0 0 " + CHART_W + " " + CHART_H + "'>"];
    svg.push("<line class='grid' x1='" + CHART_PAD_L + "' y1='" + (priceH / 2 + 2).toFixed(1) + "' x2='" + (CHART_W - CHART_PAD_R) + "' y2='" + (priceH / 2 + 2).toFixed(1) + "'/>");
    var firstSpike = null;
    for (i = 0; i < candles.length; i++) {
      if (isSpikeDay(candles[i])) { firstSpike = i; break; }
    }
    var x, cls, body, top, bot, yO, yC, volH2;
    for (i = 0; i < candles.length; i++) {
      c = candles[i];
      x = CHART_PAD_L + i * step + (step - bw) / 2;
      cls = c.c >= c.o ? "cu" : "cd";
      yO = py(c.o); yC = py(c.c);
      body = "<rect class='" + cls + "' x='" + x.toFixed(1) + "' y='" + Math.min(yO, yC).toFixed(1) +
        "' width='" + bw.toFixed(1) + "' height='" + Math.max(1, Math.abs(yC - yO)).toFixed(1) + "'/>";
      top = py(c.h); bot = py(c.l);
      body += "<line class='" + cls + "' x1='" + (x + bw / 2).toFixed(1) + "' y1='" + top.toFixed(1) +
        "' x2='" + (x + bw / 2).toFixed(1) + "' y2='" + bot.toFixed(1) + "' stroke-width='1'/>";
      volH2 = maxVol > 0 ? c.v / maxVol * volH : 0;
      var vcls = isSpikeDay(c) ? "cv-spike" : (c.c >= c.o ? "cv-up" : "cv-down");
      body += "<rect class='" + vcls + "' x='" + x.toFixed(1) + "' y='" + (priceH + 10 + (volH - volH2)).toFixed(1) +
        "' width='" + bw.toFixed(1) + "' height='" + volH2.toFixed(1) + "'/>";
      svg.push(body);
    }
    if (firstSpike !== null) {
      var lx = CHART_PAD_L + firstSpike * step + step / 2;
      svg.push("<line class='spike-line' x1='" + lx.toFixed(1) + "' y1='2' x2='" + lx.toFixed(1) + "' y2='" + (priceH + 8) + "'/>");
    }
    var yTicks = [hi - pad, (hi + lo) / 2, lo + pad];
    for (i = 0; i < yTicks.length; i++) {
      svg.push("<text class='axis' x='" + (CHART_W - CHART_PAD_R + 6) + "' y='" + (py(yTicks[i]) + 4).toFixed(1) + "'>" + fmtPx(yTicks[i]) + "</text>");
    }
    var fmtT = currentTab === "hourly" ? fmtHour : fmtDay;
    svg.push("<text class='axis' x='" + CHART_PAD_L + "' y='" + (CHART_H - 4) + "'>" + fmtT(candles[0].t) + "</text>");
    svg.push("<text class='axis' x='" + (CHART_W - CHART_PAD_R) + "' y='" + (CHART_H - 4) + "' text-anchor='end'>" + fmtT(last.t) + "</text>");
    svg.push("</svg>");
    var color = chg >= 0 ? "up" : "down";
    var chgTxt = (chg >= 0 ? "+" : "") + chg.toFixed(1) + "%";
    var intervalLabel = currentTab === "hourly" ? "1h" : "1d";
    var tabHtml = "";
    for (var key in CHART_SETS) {
      tabHtml += "<button class='chart-tab" + (key === currentTab ? " active" : "") +
        "' type='button' data-tab='" + key + "'>" + CHART_SETS[key] + "</button>";
    }
    card.innerHTML =
      "<div class='chart-head'><span class='chart-symbol'>" + symbol + "</span>" +
      "<div class='chart-tabs'>" + tabHtml + "</div>" +
      "<button class='chart-x' type='button' aria-label='Close'>&times;</button></div>" +
      "<div class='chart-sub num'>" + candles.length + " " + intervalLabel + " candles · close " +
      "<span class='" + color + "'>" + fmtPx(last.c) + " (" + chgTxt + ")</span> · vol " + fmtVol(last.q) + " USDT</div>" +
      "<div class='chart-body'>" + svg.join("") + "</div>";
  }

  function openCard(symbol) {
    current = symbol;
    if (cache[symbol]) { renderChart(symbol, cache[symbol]); return; }
    card.innerHTML = "<div class='chart-head'><span class='chart-symbol'>" + symbol + "</span><button class='chart-x' type='button' aria-label='Close'>&times;</button></div><div class='chart-sub'>loading…</div>";
    if (inflight[symbol]) return;
    inflight[symbol] = true;
    fetch("charts/" + symbol + ".json")
      .then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r.json(); })
      .then(function (payload) {
        cache[symbol] = payload;
        if (current === symbol) renderChart(symbol, payload);
      })
      .catch(function () {
        if (current === symbol) {
          card.innerHTML = "<div class='chart-head'><span class='chart-symbol'>" + symbol + "</span><button class='chart-x' type='button' aria-label='Close'>&times;</button></div><div class='chart-sub'>chart unavailable</div>";
        }
      })
      .then(function () { inflight[symbol] = false; });
  }

  function closeCard() {
    current = null;
    modal.classList.remove("visible");
    document.removeEventListener("keydown", onKey);
    if (lastFocus) { lastFocus.focus(); lastFocus = null; }
  }

  function onKey(e) {
    if (e.key === "Escape") closeCard();
  }

  function initChartCard() {
    var table = document.getElementById("spike-table");
    if (!table) return;
    table.addEventListener("click", function (e) {
      var row = e.target.closest("tr[data-chart]");
      if (!row) return;
      lastFocus = document.activeElement;
      spikeDate = row.getAttribute("data-date");
      currentTab = "daily";
      modal.classList.add("visible");
      openCard(row.getAttribute("data-symbol"));
    });
    modal.addEventListener("click", function (e) {
      if (e.target.closest(".chart-x") || !e.target.closest(".chart-card")) closeCard();
      var tab = e.target.closest(".chart-tab");
      if (tab && current) {
        currentTab = tab.getAttribute("data-tab");
        renderChart(current, cache[current]);
      }
    });
    document.addEventListener("keydown", onKey);
  }
"""


def _subscribe_js(turnstile_sitekey: str) -> str:
    if not turnstile_sitekey:
        return ""
    return r"""
  var form = document.getElementById("subscribe-form");
  var email = document.getElementById("email");
  var button = document.getElementById("subscribe-btn");
  var msg = document.getElementById("sub-msg");
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var token = window.turnstile ? window.turnstile.getResponse() : "";
    if (!email.value || !token) {
      msg.textContent = "Please enter your email and complete the verification.";
      msg.className = "sub-msg error";
      return;
    }
    button.disabled = true;
    msg.textContent = "Submitting…";
    msg.className = "sub-msg";
    fetch("/api/subscriptions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.value, turnstile: token })
    }).then(function (response) {
      if (response.status === 200) {
        msg.textContent = "Check your inbox for a confirmation link (valid 24 hours).";
        msg.className = "sub-msg ok";
        form.reset();
      } else if (response.status === 404) {
        msg.textContent = "Subscription service is unavailable right now.";
        msg.className = "sub-msg error";
      } else if (response.status === 429) {
        msg.textContent = "Too many attempts — please wait a minute and try again.";
        msg.className = "sub-msg error";
      } else {
        return response.json().then(function (data) {
          msg.textContent = data.error || "Something went wrong. Please try again.";
          msg.className = "sub-msg error";
        });
      }
    }).catch(function () {
      msg.textContent = "Subscription service is unavailable right now.";
      msg.className = "sub-msg error";
    }).finally(function () {
      button.disabled = false;
    });
  });
"""


def _status_json(latest: dict) -> dict:
    return {
        "generated_at": latest["generated_at"],
        "status": "ok",
        "data_candle_through": latest.get("data_candle_through"),
        "runtime": latest.get("runtime"),
    }


def _copy_charts(source: Path, target: Path) -> int:
    if not source.is_dir():
        return 0
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(source.glob("*.json")):
        (target / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        count += 1
    return count


def build_site(output: Path, latest: dict, site_key: str = "", charts_dir: Path | None = None) -> Path:
    """Atomically replace `output` with a fresh build; keep the previous on failure."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(output.name + ".staging")
    previous = output.with_name(output.name + ".previous")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        (staging / "data").mkdir()
        (staging / "index.html").write_text(_page_html(latest, site_key), encoding="utf-8")
        (staging / "data" / "latest.json").write_text(
            json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (staging / "status.json").write_text(
            json.dumps(_status_json(latest), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if charts_dir is not None:
            _copy_charts(Path(charts_dir), staging / "charts")
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if previous.exists():
        shutil.rmtree(previous)
    if output.exists():
        output.rename(previous)
    try:
        staging.rename(output)
    except Exception:
        if previous.exists() and not output.exists():
            previous.rename(output)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if previous.exists():
        shutil.rmtree(previous)
    return output
