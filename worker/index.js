/**
 * Shanzhai subscription + delivery Worker.
 *
 * Routes (all under /api):
 *   POST /api/subscriptions   {email, turnstile}   double opt-in subscribe
 *   GET  /api/confirm         ?e=<email>&t=<token>  activate a subscription
 *   GET  /api/unsubscribe     ?e=<email>&t=<token>  one-click unsubscribe
 *   POST /api/deliver         {secret, kind, ...}   digest / admin alert
 *
 * Secrets come from Worker bindings/secrets only — never from source.
 *   env: DB (D1), RESEND_API_KEY, TURNSTILE_SECRET, DIGEST_SECRET,
 *        FROM_EMAIL, ADMIN_EMAIL, BASE_URL
 */

const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX_ATTEMPTS = 5;
const CONFIRM_TTL_MS = 24 * 3600 * 1000;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function sha256Hex(text) {
  return crypto.subtle
    .digest("SHA-256", new TextEncoder().encode(text))
    .then((buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join(""));
}

function randomToken() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmacSha256Hex(secret, text) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(text));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function unsubscribeToken(email) {
  return hmacSha256Hex(env.DIGEST_SECRET, `unsubscribe:${email}`);
}

function constantTimeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function verifyTurnstile(token, ip) {
  const form = new URLSearchParams({
    secret: env.TURNSTILE_SECRET,
    response: token,
    remoteip: ip || "",
  });
  const res = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    body: form,
  });
  const data = await res.json();
  return data.success === true;
}

async function sendEmail(to, subject, text) {
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ from: env.FROM_EMAIL, to, subject, text }),
  });
  if (!res.ok) {
    throw new Error(`resend ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  return res.json();
}

async function rateLimited(ip) {
  if (!ip) return false;
  const cutoff = new Date(Date.now() - RATE_LIMIT_WINDOW_MS).toISOString();
  const { results } = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM subscription_attempts WHERE ip = ? AND created_at > ?"
  )
    .bind(ip, cutoff)
    .first();
  if ((results?.n ?? 0) >= RATE_LIMIT_MAX_ATTEMPTS) return true;
  await env.DB.prepare("INSERT INTO subscription_attempts (ip, created_at) VALUES (?, ?)").bind(
    ip,
    new Date().toISOString()
  ).run();
  return false;
}

async function handleSubscribe(request) {
  const ip = request.headers.get("CF-Connecting-IP") || "";
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }
  const email = String(body.email || "").trim().toLowerCase();
  const turnstile = String(body.turnstile || "");
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return json({ error: "invalid email address" }, 400);
  }
  if (!turnstile) {
    return json({ error: "missing Turnstile verification" }, 400);
  }
  if (await rateLimited(ip)) {
    return json({ error: "too many attempts — please wait a minute" }, 429);
  }
  if (!(await verifyTurnstile(turnstile, ip))) {
    return json({ error: "verification failed" }, 400);
  }

  const token = randomToken();
  const tokenHash = await sha256Hex(token);
  const now = new Date().toISOString();
  const existing = await env.DB.prepare("SELECT status FROM subscribers WHERE email = ?")
    .bind(email)
    .first();

  if (existing && existing.status === "active") {
    return json({ message: "already subscribed" });
  }
  if (existing && existing.status === "pending") {
    const created = new Date(existing.created_at).getTime();
    if (Date.now() - created < CONFIRM_TTL_MS) {
      return json({ message: "confirmation email already sent" });
    }
  }
  await env.DB.prepare(
    `INSERT INTO subscribers (email, status, token_hash, created_at)
     VALUES (?, 'pending', ?, ?)
     ON CONFLICT (email) DO UPDATE SET status = 'pending', token_hash = excluded.token_hash, created_at = excluded.created_at`
  )
    .bind(email, tokenHash, now)
    .run();

  const confirmUrl = `${env.BASE_URL}/api/confirm?e=${encodeURIComponent(email)}&t=${token}`;
  await sendEmail(
    email,
    "Confirm your Shanzhai Signal Desk subscription",
    [
      "Confirm your email to start receiving Coach signal digests.",
      "",
      confirmUrl,
      "",
      "The link expires in 24 hours. If you did not request this, ignore this email.",
    ].join("\n")
  );
  return json({ message: "confirmation email sent" });
}

async function handleConfirm(request) {
  const url = new URL(request.url);
  const email = (url.searchParams.get("e") || "").trim().toLowerCase();
  const token = url.searchParams.get("t") || "";
  if (!email || !token) return json({ error: "missing parameters" }, 400);
  const tokenHash = await sha256Hex(token);
  const row = await env.DB.prepare("SELECT * FROM subscribers WHERE email = ?").bind(email).first();
  if (!row) return json({ error: "unknown email" }, 404);
  if (row.status === "active") return json({ message: "already confirmed" });
  if (row.status === "unsubscribed") return json({ error: "subscription closed" }, 410);
  const created = new Date(row.created_at).getTime();
  if (Date.now() - created > CONFIRM_TTL_MS) {
    return json({ error: "confirmation link expired — subscribe again" }, 410);
  }
  if (!constantTimeEqual(tokenHash, row.token_hash)) {
    return json({ error: "invalid token" }, 400);
  }
  await env.DB.prepare(
    "UPDATE subscribers SET status = 'active', confirmed_at = ? WHERE email = ?"
  )
    .bind(new Date().toISOString(), email)
    .run();
  await sendEmail(
    email,
    "You are subscribed to Shanzhai Signal Desk",
    [
      "You are subscribed. One digest will be sent only when a new Coach signal is confirmed.",
      "",
      "Unsubscribe any time:",
      `${env.BASE_URL}/api/unsubscribe?e=${encodeURIComponent(email)}&t=${await unsubscribeToken(email)}`,
    ].join("\n")
  );
  return json({ message: "confirmed" });
}

async function handleUnsubscribe(request) {
  const url = new URL(request.url);
  const email = (url.searchParams.get("e") || "").trim().toLowerCase();
  const token = url.searchParams.get("t") || "";
  if (!email || !token) return json({ error: "missing parameters" }, 400);
  const row = await env.DB.prepare("SELECT * FROM subscribers WHERE email = ?").bind(email).first();
  if (!row) return json({ error: "unknown email" }, 404);
  if (row.status === "unsubscribed") return json({ message: "already unsubscribed" });
  const expected = await unsubscribeToken(email);
  if (!constantTimeEqual(token, expected)) {
    return json({ error: "invalid token" }, 400);
  }
  await env.DB.prepare(
    "UPDATE subscribers SET status = 'unsubscribed', unsubscribed_at = ? WHERE email = ?"
  )
    .bind(new Date().toISOString(), email)
    .run();
  return json({ message: "unsubscribed" });
}

function formatPrice(value) {
  const n = Number(value);
  if (n >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(4).replace(/\.?0+$/, "");
  return n.toFixed(8).replace(/\.?0+$/, "");
}

async function handleDeliver(request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }
  if (typeof body.secret !== "string" || !constantTimeEqual(body.secret, env.DIGEST_SECRET)) {
    return json({ error: "unauthorized" }, 401);
  }
  if (body.kind === "admin_alert") {
    const text = [
      `Shanzhai command ${body.command} failed:`,
      String(body.message || ""),
      `at ${body.at || ""}`,
    ].join("\n");
    await sendEmail(env.ADMIN_EMAIL, "[Shanzhai] scan failure", text);
    return json({ sent: true });
  }
  if (body.kind !== "digest") {
    return json({ error: "unknown kind" }, 400);
  }
  const signals = Array.isArray(body.signals) ? body.signals : [];
  if (!signals.length) return json({ sent: true, delivered: 0 });

  const { results: subscribers } = await env.DB.prepare(
    "SELECT email FROM subscribers WHERE status = 'active'"
  ).all();
  let delivered = 0;
  for (const subscriber of subscribers) {
    const { results: done } = await env.DB.prepare(
      "SELECT signal_key FROM deliveries WHERE email = ?"
    ).bind(subscriber.email).all();
    const doneKeys = new Set(done.map((r) => r.signal_key));
    const fresh = signals.filter((s) => s.key && !doneKeys.has(s.key));
    if (!fresh.length) continue;

    const lines = [
      `${fresh.length} new Coach breakout signal(s) on Shanzhai Signal Desk:`,
      "",
    ];
    const inserts = [];
    for (const s of fresh) {
      lines.push(
        `${s.symbol}: ${formatPrice(s.pivot_price)} pivot, close ${formatPrice(s.close)} ` +
          `(${Number(s.breakout_pct) >= 0 ? "+" : ""}${Number(s.breakout_pct).toFixed(2)}%)`
      );
      lines.push(`signal time: ${s.signal_time}`);
      lines.push("");
      inserts.push(
        env.DB.prepare(
          "INSERT OR IGNORE INTO deliveries (email, signal_key, created_at) VALUES (?, ?, ?)"
        ).bind(subscriber.email, s.key, new Date().toISOString())
      );
    }
    lines.push(env.BASE_URL);
    lines.push("");
    lines.push(
      `Unsubscribe: ${env.BASE_URL}/api/unsubscribe?e=${encodeURIComponent(subscriber.email)}&t=${await unsubscribeToken(subscriber.email)}`
    );
    await sendEmail(subscriber.email, "New Coach signal — Shanzhai Signal Desk", lines.join("\n"));
    await env.DB.batch(inserts);
    delivered += fresh.length;
  }
  return json({ sent: true, delivered });
}

export default {
  async fetch(request, requestEnv) {
    globalThis.env = requestEnv;
    const url = new URL(request.url);
    const path = url.pathname;
    try {
      if (request.method === "POST" && path === "/api/subscriptions") {
        return await handleSubscribe(request);
      }
      if (request.method === "GET" && path === "/api/confirm") {
        return await handleConfirm(request);
      }
      if (request.method === "GET" && path === "/api/unsubscribe") {
        return await handleUnsubscribe(request);
      }
      if (request.method === "POST" && path === "/api/deliver") {
        return await handleDeliver(request);
      }
      return json({ error: "not found" }, 404);
    } catch (error) {
      console.error(error);
      return json({ error: "service unavailable" }, 502);
    }
  },
};
