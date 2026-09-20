# Operations runbook

## Scheduled behavior

- `shanzhai-daily.timer`: 08:05 Asia/Shanghai, after the Binance UTC daily
  candle closes, refresh volume universe and site.
  The daily run also refreshes the hover-chart payloads (`state/charts/` →
  `public/charts/`) for the current spike symbols; chart fetch failures never
  fail the daily scan.
- `shanzhai-choch.timer`: at minutes 05, 20, 35 and 50,
  evaluate newly closed 15m triggers against confirmed 1h structure and rebuild.
  When a fresh 二次突破 signal is found, that scan immediately requests email
  delivery; other scans stay quiet. The five-minute offset ensures the 15m
  candle is closed before the anti-repaint scan runs.
  Failed requests are retained in `state/status.json` and retried next scan;
  notifications that age past the freshness window are recorded under
  `expired_notifications` instead of being silently discarded.
- A failed build leaves `public/` untouched. The status sidecar records the
  failure for the next successful build and administrator alerting.

## Manual commands

```bash
python3 -m shanzhai.cli daily --output public --state state
python3 -m shanzhai.cli charts --output public --state state
python3 -m shanzhai.cli choch --output public --state state
python3 -m shanzhai.cli choch --output public --state state
python3 -m shanzhai.cli review --output public --state state
python3 -m shanzhai.cli demo --output public
python3 -m shanzhai.cli check --output public   # exit 1 when the site or market data is stale
```

Freshness monitoring: run `check` from cron/systemd on another host or locally
hourly; it exits 1 when the published site is older than 26h or the last
closed 1h candle is older than 2h.

## Production checklist

1. Create a Python virtual environment and install `requirements.txt`.
2. Copy `deploy/shanzhai.env.example` outside the repository, fill secrets, and
   set mode 0600.
3. Install the supplied systemd units with the actual project/user paths.
4. Configure the supplied Nginx vhost and validate before reload.
5. Add Tunnel ingress for `shanzhai.shaojiang61.site` to Nginx and create its
   DNS route. Keep the terminal 404/444 ingress last.
6. Create D1 database, apply `worker/schema.sql`, configure bindings and secrets,
   then deploy the Worker route `/api/subscriptions/*`.
7. Verify page, JSON, stale-state banner, subscribe, confirm and unsubscribe.
8. Observe the next two timers and check coverage, duration and log output.

## Rollback

Disable only the new timers, restore the previous Nginx and Tunnel config from
their timestamped backups, and keep the last `public/` directory. Worker routes
can be removed without affecting the static dashboard; the form will show a
specific unavailable message. Do not delete D1 during rollback.

## Incident rules

- Coverage below 90%, no fresh daily build by 07:00, or no CHoCH scan within
  1h30m is an incident.
- Data failure: retain last-known-good, retry three times with backoff, alert the
  administrator after consecutive failure.
- Resend failure: retain and publish signal, retry `pending_notifications`
  separately on the next CHoCH scan.
- Never repair by clearing state; duplicate-delivery keys depend on it.
