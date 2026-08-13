# Shanzhai Signal Desk

Static Binance volume-spike and 4-hour Coach signal dashboard for
`shanzhai.shaojiang61.site`.

See [docs/PLAN.md](docs/PLAN.md) for the implementation plan and
[docs/RUNBOOK.md](docs/RUNBOOK.md) for deployment and operations.

## Layout

| Path | Purpose |
|---|---|
| `shanzhai/` | CLI: `domain.py` (signal logic), `binance_api.py`, `pipeline.py`, `site.py` (static build), `notify.py`, `cli.py` |
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
.venv/bin/python -m shanzhai.cli daily --output public --state state
.venv/bin/python -m shanzhai.cli coach --output public --state state
.venv/bin/python -m shanzhai.cli check --output public   # non-zero when stale
```

The `coach` command requests digest delivery only when new signals exist and
`WORKER_URL`/`DIGEST_SECRET` are configured in the environment (see
`deploy/shanzhai.env.example`).

## Worker

```bash
cd worker
npx wrangler d1 execute shanzhai --file schema.sql
npx wrangler deploy
```

Worker secrets and bindings are listed in `deploy/shanzhai.env.example`; the
route `/api/subscriptions/*` must be attached to the site hostname so the
subscription API and the static page share an origin.
