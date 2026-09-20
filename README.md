# Shanzhai Signal Desk

Static Binance signal dashboard for `shanzhai.shaojiang61.site`: a volume-spike
universe plus 1-hour **BOS/CHoCH** swing-layer breakouts, AI-reviewed after
24 hours, with immediate HTML email alerts for eligible signals.

See [docs/PLAN.md](docs/PLAN.md) for the implementation plan and
[docs/RUNBOOK.md](docs/RUNBOOK.md) for deployment and operations.

## Features

- **Volume spikes** — daily-candle volume surge filter (base volume ≥5x the
  preceding 7-candle average) defines the watch pool.
- **Hover chart** — hovering a spike symbol shows its last 90 closed daily
  candles (inline SVG, no external libs) with the spike day highlighted; chart
  payloads refresh daily with the `daily` scan into `public/charts/`.
- **CHoCH breakouts** — LuxAlgo-style SMC: structure levels are confirmed on
  1h candles (right-confirmed pivots, non-repainting); a 15m close crossing the
  active level fires a BOS/CHoCH. A symbol's **first** bullish breakout only
  updates the dashboard; the **second** one (二次突破) becomes email-eligible.
- **AI 复盘 (AI review)** — every swing signal is evaluated 24h after trigger
  by an LLM (verdict hit/partial/miss + confidence + one-line reason) and shown
  on the dashboard with a hit-rate summary.
- **Email alerts** — when a new 二次突破 (second breakout of a symbol) is
  confirmed, the `choch` scan immediately requests an HTML email via a
  Cloudflare Worker + D1 (subscribe/confirm/unsubscribe). The first breakout
  of a symbol stays dashboard-only.
- **Deterministic CLI** — stdlib-only production pipeline; candles close
  before anything is emitted (anti-repaint by design).

## Layout

| Path | Purpose |
|---|---|
| `shanzhai/` | CLI: `smc.py` (SMC engine), `domain.py`, `binance_api.py`, `pipeline.py`, `site.py` (static build), `charts.py` (daily hover-chart payloads), `review.py` (AI 复盘), `notify.py`, `io_utils.py`, `cli.py` |
| `worker/` | Cloudflare Worker (subscriptions, confirm, unsubscribe, signal delivery) + D1 schema |
| `deploy/` | systemd units/timers, Nginx vhost, cloudflared ingress, env template |
| `tests/` | pytest suite (`pytest` runs offline against a fake Binance client) |
| `docs/` | PLAN.md and RUNBOOK.md |
| `find spike volume with plot.ipynb` | research artifact — production uses the deterministic CLI |

The production CLI is stdlib-only; `requirements.txt` covers the notebook
runtime only.

## 上游项目与许可证

本项目基于 [RuiRuiPing/Find-Spike-Volume](https://github.com/RuiRuiPing/Find-Spike-Volume)
构建，并在其基础上扩展了 CHoCH/SMC 结构分析、15 分钟触发、AI 复盘、邮件通知和
Cloudflare Worker 订阅功能。上游项目采用 MIT License；本仓库保留其版权声明和许可
条款，完整内容见 [LICENSE](LICENSE)。

## Local build

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m shanzhai.cli demo --output public --state state
.venv/bin/python -m pytest tests/ -q
python3 -m http.server 8088 --directory public
```

Live scan commands (need outbound access to Binance; no API key for public
market data):

```bash
.venv/bin/python -m shanzhai.cli daily --output public --state state   # volume spikes + chart payloads
.venv/bin/python -m shanzhai.cli choch --output public --state state   # CHoCH scan + immediate signal delivery
.venv/bin/python -m shanzhai.cli charts --output public --state state  # refresh hover-chart payloads only
.venv/bin/python -m shanzhai.cli review --output public --state state  # AI 复盘
.venv/bin/python -m shanzhai.cli check --output public                 # non-zero when stale
```

The `choch` command requests email delivery immediately when a fresh,
email-eligible 二次突破 is detected. It needs `WORKER_URL`/`DIGEST_SECRET`
configured in the environment (see `deploy/shanzhai.env.example`). Failed
delivery requests remain pending and are retried by the next `choch` scan;
expired pending entries are recorded in `expired_notifications`. The `review`
command needs the AI config
(`AI_BASE_URL`/`AI_MODEL`/`AI_API_KEY`) and only evaluates matured swing
signals (≥24h old); stale/duplicate signals are never emailed.

## Worker

```bash
cd worker
npx wrangler d1 execute shanzhai --file schema.sql
npx wrangler deploy
```

Worker secrets and bindings are listed in `deploy/shanzhai.env.example`; the
route `/api/*` must be attached to the site hostname so the subscription API
and the static page share an origin.
