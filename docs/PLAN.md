# Shanzhai Signal Desk — implementation and handoff plan

Status: implementation in this repository; production activation still requires
Cloudflare/Resend credentials and privileged host configuration.

## 1. Goal

Publish a static, mobile-friendly market dashboard at
`https://shanzhai.shaojiang61.site`. At 06:00 Asia/Shanghai it refreshes the
daily volume-spike universe. Five minutes after every closed Binance 1-hour
candle it evaluates CHoCH breakouts, republishes the site, and sends one digest
only when new signals exist.

The existing notebook remains a research artifact. Production uses the same
market concepts through a deterministic CLI because notebook stdout and a
multi-page PDF are not a safe application data contract.

## 2. Locked product decisions

- Volume spike v2: among the last 10 closed daily candles, use the newest candle
  whose base volume exceeds its preceding 7-candle average by 5x. Rank by the
  ratio and also display USDT quote volume.
- Watch pool: a qualifying pair remains eligible for CHoCH for 10 days.
- CHoCH: structure levels are confirmed on 1h candles (right-confirmed pivots,
  non-repainting, size 5); a 15m close crossing the active level fires a
  BOS (continuation) or CHoCH (reversal) against the running trend bias. A level
  only upgrades when a later pivot exceeds it, and each level fires at most once.
- Email gate (二次突破): a symbol's first bullish breakout only updates the
  dashboard; the second bullish breakout of the same symbol within the recent
  window becomes email-eligible and is marked 二次突破. Signals below the gate
  are still emitted to the page and history so the dashboard reflects every
  confirmed crossing.
- Idempotency key: symbol, interval and pivot candle open time. A newer confirmed
  pivot may trigger a later signal.
- Initial import records history but sends no historical mail. Recovery scans
  missing candles and emails at most one digest for signals no older than 24h.
- Digest delivery: one merged digest per day at `DIGEST_HOUR` (default 08:00
  Asia/Shanghai), collecting email-eligible signals since the previous digest
  and collapsing repeated same-direction signals per symbol into a single entry
  (24h cooldown). `last_digest_at` advances only on a successful send.
- Public read-only dashboard. Email subscription uses double opt-in, Turnstile,
  one-click unsubscribe and rate limiting.
- Browser times use Asia/Shanghai and always show both page update time and the
  last fully closed market candle.
- Keep the last known good site on scan/build failure. Never publish an empty or
  partial result as success.

## 3. Architecture

```text
Binance public REST
       |
 Python scan CLI ---- state/*.json (private runtime state)
       |                         |
       +---- atomic build ---- public/ (static, last-known-good)
                                  |
                      Nginx :PORT -> Cloudflare Tunnel
                                  |
                     shanzhai.shaojiang61.site
                                  |
                  /api/subscriptions/* intercepted by Worker
                                  |
                         Turnstile + D1 + Resend
```

Data generation and notification are deliberately separate: publication can
succeed when Resend is unavailable, while notification failures remain visible
in status and retry logs.

## 4. Data contract

`public/data/latest.json` contains:

- `schema_version`, `algorithm_version`, `generated_at`, `timezone`;
- `status`, `data_candle_through`, runtime duration and symbol success counts;
- `volume_spikes` with symbol, date, ratio, base volume and quote volume;
- `choch.signals`, `choch.latest_scan_at` and `choch.new_signal_count`;
- a bounded 30-day CHoCH history for the UI.

Private notification state and subscriber data never enter `public/`.

## 5. Visual system

The page follows `DESIGN.md`: near-black `#0b0e11`, flat card layers
`#1e2329/#2b3139`, one yellow accent `#fcd535`, green/red only for market
direction, thin borders, restrained radii, tabular financial figures and a
light closing footer. The signature element is the CHoCH breakout trace: a
quiet mini price line crossing a yellow pivot rail. No gradients, glow or large
red/green surfaces.

Desktop uses an 8:4 dashboard split; tablet uses two columns; mobile becomes a
single column with horizontally scrollable tables. Focus indicators, textual
signal labels, 44px targets and reduced-motion support are mandatory.

## 6. Delivery phases and acceptance

1. Pipeline: unit-tested pivot/breakout, closed-candle filtering, volume v2 and
   idempotency. Partial market coverage below 90% fails the publish.
2. Static build: valid latest JSON, responsive page, update/staleness state,
   CHoCH history, report link and subscription states.
3. Worker: verified Turnstile, hashed tokens, double opt-in, unsubscribe,
   throttling and Resend integration; no secrets in source.
4. Operations: systemd oneshot/timers, atomic deployment, bounded logs,
   Nginx/Tunnel routing and administrator failure notification.
5. Production: DNS/Tunnel/Nginx/Worker/D1/Resend configured; run a synthetic
   subscription and unsubscribe; verify one full 1h scan and next 06:00 scan.

## 7. Risks and optimizations

- Binance may rate-limit or block the host region. Use bounded retries, cache
  exchange metadata and expose coverage. A proxy is an operational fallback,
  not embedded application behavior.
- Scanning every pair serially is slow. Use conservative bounded concurrency
  while respecting Binance weights; cache unchanged daily data between CHoCH
  scans.
- A structure is confirmed only by the candles that follow it. This is intentional
  anti-repainting behavior and should be stated in the UI.
- Add parameter backtests before changing 5x, 10-day or pivot settings. Store an
  `algorithm_version` with every result.
- Later improvements: per-symbol subscriptions, weekly precision review,
  liquidity/minimum-history filters, stablecoin/leveraged-token exclusions,
  delivery health dashboard and signed JSON snapshots.

## 8. Secrets and external prerequisites

Required: Cloudflare account access, Worker/D1/Turnstile identifiers, Resend API
key and verified sender domain, administrator email. Host changes require sudo
for Nginx, `/etc/cloudflared/config.yml`, and systemd. Values belong in Worker
secrets or a root-readable environment file—not this repository.

