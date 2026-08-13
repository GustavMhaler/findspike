# Shanzhai Signal Desk

Static Binance signal dashboard for `shanzhai.shaojiang61.site`: a volume-spike
universe plus 1-hour **BOS/CHoCH** swing-layer breakouts, AI-reviewed after
24 hours, delivered as HTML email digests.

See [docs/PLAN.md](docs/PLAN.md) for the implementation plan and
[docs/RUNBOOK.md](docs/RUNBOOK.md) for deployment and operations.

## Features

- **Volume spikes** — daily-candle volume surge filter (base volume ≥5x the
  preceding 7-candle average) defines the watch pool.
- **CHoCH breakouts** — LuxAlgo-style SMC on 1h candles: swing layer (50)
  confirmed structure levels, BOS/CHoCH breaks; internal-layer signals are
  computed but only swing-layer signals are emitted (page, history, email,
  reviews).
- **AI 复盘 (AI review)** — every swing signal is evaluated 24h after trigger
  by an LLM (verdict hit/partial/miss + confidence + one-line reason) and shown
  on the dashboard with a hit-rate summary.
- **Email digests** — HTML digests (Chinese, Asia/Shanghai time) with new
  swing signals only, via a Cloudflare Worker + D1 (subscribe/confirm/
  unsubscribe).
- **Deterministic CLI** — stdlib-only production pipeline; candles close
  before anything is emitted (anti-repaint by design).

## Layout

| Path | Purpose |
|---|---|
| `shanzhai/` | CLI: `smc.py` (SMC engine), `domain.py`, `binance_api.py`, `pipeline.py`, `site.py` (static build), `review.py` (AI 复盘), `notify.py`, `io_utils.py`, `cli.py` |
| `worker/` | Cloudflare Worker (subscriptions, confirm, unsubscribe, digest delivery) + D1 schema |
| `deploy/` | systemd units/timers, Nginx vhost, cloudflared ingress, env template |
| `tests/` | pytest suite (`pytest` runs offline against a fake Binance client) |
| `docs/` | PLAN.md and RUNBOOK.md |
| `find spike volume with plot.ipynb` | research artifact — production uses the deterministic CLI |

The production CLI is stdlib-only; `requirements.txt` covers the notebook
runtime only.

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
.venv/bin/python -m shanzhai.cli daily --output public --state state   # volume spikes
.venv/bin/python -m shanzhai.cli choch --output public --state state   # CHoCH scan + digest delivery
.venv/bin/python -m shanzhai.cli review --output public --state state  # AI 复盘
.venv/bin/python -m shanzhai.cli check --output public                 # non-zero when stale
```

The `choch` command requests digest delivery only when new signals exist and
`WORKER_URL`/`DIGEST_SECRET` are configured in the environment (see
`deploy/shanzhai.env.example`). The `review` command needs the AI config
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
