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

async function sendEmail(to, subject, text, html) {
  const body = { from: env.FROM_EMAIL, to, subject, text };
  if (html) body.html = html;
  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
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
      "Confirm your email to start receiving CHoCH signal digests.",
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
      "You are subscribed. One digest will be sent only when a new CHoCH signal is confirmed.",
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
      `Shanzhai 命令 ${body.command} 执行失败：`,
      String(body.message || ""),
      `时间：${body.at || ""}`,
    ].join("\n");
    await sendEmail(env.ADMIN_EMAIL, "[Shanzhai] 扫描失败", text);
    return json({ sent: true });
  }
  if (body.kind !== "digest") {
    return json({ error: "unknown kind" }, 400);
  }
  const signals = Array.isArray(body.signals) ? body.signals : [];
  if (!signals.length) return json({ sent: true, delivered: 0 });
  // Noise control: only swing-layer signals are emailed; internal-layer
  // signals stay visible on the dashboard page only.
  const swingSignals = signals.filter((s) => s.layer === "swing");
  if (!swingSignals.length) return json({ sent: true, delivered: 0, note: "internal only" });

  const { results: subscribers } = await env.DB.prepare(
    "SELECT email FROM subscribers WHERE status = 'active'"
  ).all();
  let delivered = 0;
  for (const subscriber of subscribers) {
    const { results: done } = await env.DB.prepare(
      "SELECT signal_key FROM deliveries WHERE email = ?"
    ).bind(subscriber.email).all();
    const doneKeys = new Set(done.map((r) => r.signal_key));
    const fresh = swingSignals.filter((s) => s.key && !doneKeys.has(s.key));
    if (!fresh.length) continue;

    const subject = `Shanzhai 信号：${fresh.length} 个新 CHoCH（${fresh.filter((s) => s.direction === "bullish").length} 涨 ${fresh.filter((s) => s.direction === "bearish").length} 跌）`;
    const unsubscribeUrl = `${env.BASE_URL}/api/unsubscribe?e=${encodeURIComponent(subscriber.email)}&t=${await unsubscribeToken(subscriber.email)}`;
    const inserts = fresh.map((s) =>
      env.DB.prepare(
        "INSERT OR IGNORE INTO deliveries (email, signal_key, created_at) VALUES (?, ?, ?)"
      ).bind(subscriber.email, s.key, new Date().toISOString())
    );
    await sendEmail(subscriber.email, subject, digestText(fresh, unsubscribeUrl), digestHtml(fresh, unsubscribeUrl));
    await env.DB.batch(inserts);
    delivered += fresh.length;
  }
  return json({ sent: true, delivered });
}

function beijingTime(iso) {
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(new Date(iso));
  } catch {
    return String(iso);
  }
}

function escHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

function digestText(signals, unsubscribeUrl) {
  const lines = [`Shanzhai 新信号 ${signals.length} 个（${signals.filter((s) => s.direction === "bullish").length} 涨 ${signals.filter((s) => s.direction === "bearish").length} 跌）：`, ""];
  for (const s of signals) {
    const arrow = s.direction === "bullish" ? "▲" : "▼";
    const verb = s.direction === "bullish" ? "突破" : "跌破";
    const move = (Number(s.breakout_pct) >= 0 ? "+" : "") + Number(s.breakout_pct).toFixed(2) + "%";
    lines.push(`${arrow} ${s.symbol} ${verb} ${formatPrice(s.level)}，收 ${formatPrice(s.close)}（${move}）`);
    lines.push(`   时间：${beijingTime(s.signal_time)}`);
    lines.push("");
  }
  lines.push(`${env.BASE_URL}（查看图表）`);
  lines.push("");
  lines.push("BOS = 顺势延续，CHoCH = 走势反转。");
  lines.push(`退订：${unsubscribeUrl}`);
  return lines.join("\n");
}

function digestHtml(signals, unsubscribeUrl) {
  const up = signals.filter((s) => s.direction === "bullish").length;
  const down = signals.length - up;
  const rows = signals
    .map((s) => {
      const bullish = s.direction === "bullish";
      const color = bullish ? "#0ecb81" : "#f6465d";
      const arrow = bullish ? "▲" : "▼";
      const verb = bullish ? "突破" : "跌破";
      const move = (Number(s.breakout_pct) >= 0 ? "+" : "") + Number(s.breakout_pct).toFixed(2) + "%";
      return `<tr>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;font-weight:600;white-space:nowrap">${escHtml(s.symbol)}</td>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;color:${color};white-space:nowrap">${arrow} ${bullish ? "看涨" : "看跌"}</td>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;white-space:nowrap">${escHtml(s.tag)}</td>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;white-space:nowrap">${verb} <b>${formatPrice(s.level)}</b></td>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;white-space:nowrap">${formatPrice(s.close)}</td>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;color:${color};white-space:nowrap">${move}</td>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;white-space:nowrap">${beijingTime(s.signal_time)}</td>
      </tr>`;
    })
    .join("");
  return `<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f7f8fa;font-family:'Helvetica Neue',Arial,sans-serif;font-size:14px;color:#181a20">
<div style="max-width:640px;margin:0 auto;padding:24px 16px">
  <div style="background:#0b0e11;color:#eaecef;border-radius:12px;padding:20px 24px;margin-bottom:16px">
    <div style="font-size:18px;font-weight:700;color:#fcd535">Shanzhai 信号速报</div>
    <div style="margin-top:6px;color:#929aa5">${signals.length} 个新信号 · <span style="color:#0ecb81">${up} 涨</span> · <span style="color:#f6465d">${down} 跌</span></div>
  </div>
  <div style="background:#ffffff;border:1px solid #eceff3;border-radius:12px;overflow:hidden">
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#fafafa;color:#707a8a;font-size:12px">
        <th style="text-align:left;padding:9px 12px">币种</th>
        <th style="text-align:left;padding:9px 12px">方向</th>
        <th style="text-align:left;padding:9px 12px">类型</th>
        <th style="text-align:left;padding:9px 12px">价位</th>
        <th style="text-align:left;padding:9px 12px">收盘</th>
        <th style="text-align:left;padding:9px 12px">幅度</th>
        <th style="text-align:left;padding:9px 12px">时间(北京)</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
  <p style="font-size:12px;color:#707a8a;line-height:1.7">
    说明：BOS = 顺势延续，CHoCH = 走势反转（结构变化）。信号基于已收盘的 1 小时 K 线，结构确认后不会重绘。仅供研究参考，不构成投资建议。
  </p>
  <p style="font-size:13px"><a href="${escHtml(env.BASE_URL)}" style="color:#f0b90b;font-weight:600">打开仪表盘查看图表 →</a></p>
  <p style="font-size:12px;color:#929aa5"><a href="${escHtml(unsubscribeUrl)}" style="color:#929aa5">退订通知</a> · 仅在新信号确认时发送，不会每天打扰</p>
</div>
</body></html>`;
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
