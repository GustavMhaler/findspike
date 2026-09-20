# Operations runbook

## Scheduled behavior

- `shanzhai-daily.timer`: 08:05 Asia/Shanghai, after the Binance UTC daily
  candle closes, refresh volume universe and site.
  The daily run also refreshes the hover-chart payloads (`state/charts/` →
  `public/charts/`) for the current spike symbols; chart fetch failures never
  fail the daily scan.
- `shanzhai-choch.timer`: at minutes 05, 20, 35 and 50,
  evaluate newly closed 15m triggers against confirmed 1h structure and rebuild.
  When a fresh breakout is found, that scan immediately requests delivery:
  email receives only bullish 二次突破, while QQ receives bullish 首次 and
  二次突破 signals. Bearish signals remain dashboard-only. Other scans stay
  quiet. The five-minute offset ensures
  the 15m candle is closed before the anti-repaint scan runs.
  Failed requests are retained in `state/status.json` and retried next scan;
  notifications that age past the freshness window are recorded under
  `expired_notifications` or `expired_qq_notifications` instead of being
  silently discarded.
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
   then deploy the Worker route `/api/*`. Configure `QQ_APP_ID` and
   `QQ_APP_SECRET` as Worker secrets and set the QQ Open Platform callback to
   `https://shanzhai.shaojiang61.site/api/qq/events`, with C2C private-message
   and group @ message events enabled.
7. Verify page, JSON, stale-state banner, email subscribe/confirm/unsubscribe,
   then add the QQ robot to a permitted group. Confirm that `@机器人 订阅`
   enables group delivery and that `@机器人 取消订阅` disables it for a
   group admin/owner. Private subscriptions can be tested separately by
   sending the bot a private message.
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
- QQ delivery failure: retain the signal in `qq_deliveries` only after a
  successful send; failed sends release their claim and are retried with the
  next delivery request. Group delivery uses the same rule in
  `qq_group_deliveries`. QQ `user_openid`/`group_openid` values are AppID-scoped
  and never expose the user's QQ number or numeric group ID.
- Never repair by clearing state; duplicate-delivery keys depend on it.
