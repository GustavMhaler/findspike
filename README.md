# Shanzhai Signal Desk

Static Binance signal dashboard for `shanzhai.shaojiang61.site`: a volume-spike
universe plus 1-hour **BOS/CHoCH** swing-layer breakouts, AI-reviewed after
24 hours, with immediate email and QQ private-message alerts for eligible
signals.

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
- **QQ Bot alerts** — the same Worker receives verified QQ C2C webhook events,
  records each user's `user_openid`, and sends fresh 首次/二次突破 signals by QQ
  private message. Unlike email, QQ also receives fresh bullish first
  breakouts. Bearish signals remain dashboard-only. It also supports group
  subscriptions through
  `GROUP_AT_MESSAGE_CREATE`: `@机器人 订阅` enables a group's alerts, and
  `@机器人 取消订阅` (群主/管理员) disables them.
- **Deterministic CLI** — stdlib-only production pipeline; candles close
  before anything is emitted (anti-repaint by design).

## Layout

| Path | Purpose |
|---|---|
| `shanzhai/` | CLI: `smc.py` (SMC engine), `domain.py`, `binance_api.py`, `pipeline.py`, `site.py` (static build), `charts.py` (daily hover-chart payloads), `review.py` (AI 复盘), `notify.py`, `io_utils.py`, `cli.py` |
| `worker/` | Cloudflare Worker (email/QQ subscriptions and signal delivery) + D1 schema |
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

The `choch` command scans every 15 minutes and requests delivery immediately
when a fresh breakout is detected. Email remains limited to bullish 二次突破;
QQ receives fresh bullish 首次 or 二次突破 signals; bearish signals stay
dashboard-only. It needs
`WORKER_URL`/`DIGEST_SECRET` configured in the environment (see
`deploy/shanzhai.env.example`). Failed delivery requests remain pending and
are retried by the next `choch` scan; expired pending entries are recorded in
`expired_notifications` and `expired_qq_notifications`. The `review` command
needs the AI config
(`AI_BASE_URL`/`AI_MODEL`/`AI_API_KEY`) and only evaluates matured swing
signals (≥24h old); stale/duplicate signals are never emailed.

## Worker

```bash
cd worker
npx wrangler d1 execute shanzhai --remote --file schema.sql
npx wrangler deploy
```

Worker secrets and bindings are listed in `deploy/shanzhai.env.example`; the
route `/api/*` must be attached to the site hostname so the subscription API
and the static page share an origin.

### QQ Bot setup

1. Apply the updated D1 schema and deploy the Worker:

   ```bash
   npx wrangler d1 execute shanzhai --remote --file schema.sql
   npx wrangler secret put QQ_APP_ID
   npx wrangler secret put QQ_APP_SECRET
   npx wrangler deploy
   ```

2. In QQ Open Platform, configure the event callback URL as
   `https://shanzhai.shaojiang61.site/api/qq/events`, choose the C2C private
   message and group @ message events, and complete the platform's callback
   validation. The Worker performs Ed25519 signature verification using
   `QQ_APP_SECRET`.
3. Add the QQ robot to a permitted QQ group. In the group, mention the bot
   and send `订阅`; it replies with the group subscription status and enables
   signal delivery. A group admin/owner can send `取消订阅` to stop it. The
   QQ client must allow the robot's主动消息; otherwise QQ may reject later
   proactive alerts. Private-message subscriptions continue to work too.

`QQ_APP_ID` and `QQ_APP_SECRET` are Worker secrets only. Do not put either
value in the repository or in `/etc/shanzhai.env`; the existing `choch`
delivery request is automatically fanned out to active QQ subscribers.
