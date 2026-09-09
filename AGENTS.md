# AGENTS.md

Shanzhai Signal Desk — static Binance signal dashboard. Python stdlib-only CLI
(`shanzhai/`) builds `public/` from `state/`; a Cloudflare Worker (`worker/`)
delivers email digests. The live Binance pipeline runs on systemd timers
(`deploy/`) — `daily` at 06:00 and `choch` hourly, Asia/Shanghai.

## Verify loop

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check shanzhai tests
```

A change is done when both are green. Tests run fully offline against
`FakeClient` (tests/conftest.py) — no test hits the network; add network
behaviour to `FakeClient` rather than mocking per-test.

## Invariants

- **Anti-repaint.** Signals emit from closed candles only (daily candles at
  00:00 UTC, 1h/15m right-confirmed). Keep it: fetches drop the forming
  candle, and a forming-candle path silently reintroduces repainting.
- **Atomic build.** `build_site` writes a staging directory, then swaps into
  `public/`; a failed build leaves the old site serving. Never write `public/`
  in place — including "just fix this one file" edits.
- **Failure isolation.** Per-symbol fetch failures are collected, never
  raised: charts, review, and notify are auxiliary to the daily scan and a
  failure there never fails the scan. Record it (`state/status.json`) and move on.
- **Charts source of truth.** `state/charts/<SYMBOL>.json` is written by the
  daily run; `build_site(charts_dir=...)` copies it into `public/charts/`.
  `update_charts` takes the state root — `chart_path` joins `charts/` itself.

## Hazards

- **f-string braces.** All page CSS/JS lives inside the `_page_html` f-string
  in `site.py`: every literal `{`/`}` must be doubled. Standalone JS goes in a
  plain-string helper (see `_chart_js`) instead of growing the f-string.
- **`public/` is a build artifact** and `state/` is runtime state — neither is
  hand-edited; fix the generator.

## More

- [README.md](README.md) — features, layout, commands, env vars
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — deployment, systemd units, ops drills
- [docs/PLAN.md](docs/PLAN.md) — the original implementation plan
