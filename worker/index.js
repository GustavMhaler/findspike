/**
 * Shanzhai subscription + delivery Worker.
 *
 * Routes (all under /api):
 *   POST /api/subscriptions   {email, turnstile}   double opt-in subscribe
 *   GET  /api/confirm         ?e=<email>&t=<token>  activate a subscription
 *   GET  /api/unsubscribe     ?e=<email>&t=<token>  one-click unsubscribe
 *   POST /api/deliver         {secret, kind, ...}   signal email / admin alert
 *   POST /api/qq/events       QQ Bot webhook events and C2C/group subscriptions
 *
 * Secrets come from Worker bindings/secrets only — never from source.
 *   env: DB (D1), RESEND_API_KEY, TURNSTILE_SECRET, DIGEST_SECRET,
 *        FROM_EMAIL, ADMIN_EMAIL, BASE_URL, QQ_APP_ID, QQ_APP_SECRET
 */

const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX_ATTEMPTS = 5;
const CONFIRM_TTL_MS = 24 * 3600 * 1000;
const SIGNAL_MAX_AGE_MS = 26 * 3600 * 1000;
const QQ_API_BASE = "https://api.bot.qq.com";
const QQ_MAX_MESSAGE_LENGTH = 3500;

let qqAccessTokenCache = { token: "", expiresAt: 0 };
let qqSigningKeyPromise;

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

function qqSecretSeed(secret) {
  if (!secret) throw new Error("QQ_APP_SECRET is not configured");
  let seed = "";
  while (seed.length < 32) seed += secret;
  return new TextEncoder().encode(seed.slice(0, 32));
}

function qqPrivateKeyDer(secret) {
  // PKCS#8 wrapper for an Ed25519 private key whose 32-byte seed is the
  // QQ Bot Secret repeated/truncated per the platform's signing spec.
  const prefix = Uint8Array.from([
    0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06,
    0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
  ]);
  const seed = qqSecretSeed(secret);
  const der = new Uint8Array(prefix.length + seed.length);
  der.set(prefix);
  der.set(seed, prefix.length);
  return der;
}

async function qqSigningKey(secret) {
  if (!qqSigningKeyPromise) {
    qqSigningKeyPromise = crypto.subtle.importKey(
      "pkcs8",
      qqPrivateKeyDer(secret),
      { name: "Ed25519" },
      true,
      ["sign"]
    );
  }
  return qqSigningKeyPromise;
}

function hexBytes(value) {
  if (!/^[0-9a-f]{128}$/i.test(value)) return null;
  const bytes = new Uint8Array(64);
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(value.slice(i * 2, i * 2 + 2), 16);
  return bytes;
}

async function verifyQQSignature(secret, timestamp, signature, body) {
  const sig = hexBytes(signature);
  if (!sig || !timestamp) return false;
  try {
    const privateKey = await qqSigningKey(secret);
    const jwk = await crypto.subtle.exportKey("jwk", privateKey);
    const publicKey = await crypto.subtle.importKey(
      "jwk",
      { kty: "OKP", crv: "Ed25519", x: jwk.x, ext: true },
      { name: "Ed25519" },
      false,
      ["verify"]
    );
    return crypto.subtle.verify(
      "Ed25519",
      publicKey,
      sig,
      new TextEncoder().encode(`${timestamp}${body}`)
    );
  } catch (error) {
    console.error("QQ signature verification failed", error);
    return false;
  }
}

async function qqSign(secret, text) {
  const signature = await crypto.subtle.sign(
    "Ed25519",
    await qqSigningKey(secret),
    new TextEncoder().encode(text)
  );
  return [...new Uint8Array(signature)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function qqAccessToken(requestEnv) {
  const now = Date.now();
  if (qqAccessTokenCache.token && qqAccessTokenCache.expiresAt > now + 60_000) {
    return qqAccessTokenCache.token;
  }
  if (!requestEnv.QQ_APP_ID || !requestEnv.QQ_APP_SECRET) {
    throw new Error("QQ_APP_ID/QQ_APP_SECRET are not configured");
  }
  const response = await fetch(`${QQ_API_BASE}/app/getAppAccessToken`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ appId: requestEnv.QQ_APP_ID, clientSecret: requestEnv.QQ_APP_SECRET }),
  });
  const data = await response.json();
  if (!response.ok || data.code || !data.access_token) {
    throw new Error(`QQ access token failed: ${data.code || response.status}`);
  }
  const expiresIn = Number(data.expires_in) || 7200;
  qqAccessTokenCache = {
    token: data.access_token,
    expiresAt: now + Math.max(60, expiresIn - 60) * 1000,
  };
  return qqAccessTokenCache.token;
}

async function qqApi(requestEnv, path, body) {
  let token = await qqAccessToken(requestEnv);
  let response = await fetch(`${QQ_API_BASE}${path}`, {
    method: "POST",
    headers: {
      Authorization: `QQBot ${token}`,
      "Content-Type": "application/json; charset=utf-8",
    },
    body: JSON.stringify(body),
  });
  if (response.status === 401) {
    qqAccessTokenCache = { token: "", expiresAt: 0 };
    token = await qqAccessToken(requestEnv);
    response = await fetch(`${QQ_API_BASE}${path}`, {
      method: "POST",
      headers: {
        Authorization: `QQBot ${token}`,
        "Content-Type": "application/json; charset=utf-8",
      },
      body: JSON.stringify(body),
    });
  }
  const data = response.status === 204 ? {} : await response.json();
  if (!response.ok || (data.err_code !== undefined && data.err_code !== 0)) {
    throw new Error(`QQ API ${path} failed: ${data.err_code || response.status}`);
  }
  return data;
}

async function sendQQText(userOpenid, content, requestEnv, replyTo = "") {
  const body = { content, msg_type: 0 };
  if (replyTo) {
    body.msg_id = replyTo;
    body.msg_seq = 1;
  }
  return qqApi(requestEnv, `/v2/users/${encodeURIComponent(userOpenid)}/messages`, body);
}

async function sendQQGroupText(groupOpenid, content, requestEnv, replyTo = "") {
  const body = { content, msg_type: 0 };
  if (replyTo) {
    body.msg_id = replyTo;
    body.msg_seq = 1;
  }
  return qqApi(requestEnv, `/v2/groups/${encodeURIComponent(groupOpenid)}/messages`, body);
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

function qqUserOpenid(event) {
  return String(
    event?.author?.user_openid
      || event?.author?.id
      || event?.user_openid
      || event?.openid
      || ""
  ).trim();
}

function qqGroupOpenid(event) {
  return String(event?.group_openid || event?.group?.openid || "").trim();
}

function qqMessageCommand(content) {
  return String(content || "").trim().replace(/\s+/g, "").toLowerCase();
}

async function upsertQQSubscriber(userOpenid, now) {
  await env.DB.prepare(
    `INSERT INTO qq_subscribers (user_openid, status, created_at, updated_at, last_message_at)
     VALUES (?, 'active', ?, ?, ?)
     ON CONFLICT (user_openid) DO UPDATE SET
       status = 'active', updated_at = excluded.updated_at, last_message_at = excluded.last_message_at`
  )
    .bind(userOpenid, now, now, now)
    .run();
}

async function upsertQQGroup(groupOpenid, now) {
  await env.DB.prepare(
    `INSERT INTO qq_groups (group_openid, status, created_at, updated_at, last_message_at)
     VALUES (?, 'active', ?, ?, ?)
     ON CONFLICT (group_openid) DO UPDATE SET
       status = 'active', updated_at = excluded.updated_at, last_message_at = excluded.last_message_at`
  )
    .bind(groupOpenid, now, now, now)
    .run();
}

function qqGroupAdmin(event) {
  return ["admin", "owner"].includes(event?.author?.member_role);
}

async function processQQGroupEvent(event, requestEnv) {
  const groupOpenid = qqGroupOpenid(event);
  if (!groupOpenid) return;
  const now = new Date().toISOString();
  const command = qqMessageCommand(event.content);

  if (["取消订阅", "退订", "unsubscribe", "stop", "关闭"].includes(command)) {
    if (!qqGroupAdmin(event)) {
      if (event.id) {
        await sendQQGroupText(
          groupOpenid,
          "只有群主或管理员可以关闭本群推送。",
          requestEnv,
          event.id
        );
      }
      return;
    }
    await env.DB.prepare(
      "INSERT INTO qq_groups (group_openid, status, created_at, updated_at, last_message_at) VALUES (?, 'unsubscribed', ?, ?, ?) "
      + "ON CONFLICT (group_openid) DO UPDATE SET status = 'unsubscribed', updated_at = excluded.updated_at, last_message_at = excluded.last_message_at"
    ).bind(groupOpenid, now, now, now).run();
    if (event.id) {
      await sendQQGroupText(groupOpenid, "本群 QQ 推送已关闭。群主或管理员发送“订阅”即可恢复。", requestEnv, event.id);
    }
    return;
  }

  // The first @ message is the group opt-in. This works for personal bots,
  // whose QQ platform permissions may prevent ordinary users from adding the
  // bot as a private friend.
  await upsertQQGroup(groupOpenid, now);
  if (!event.id) return;

  let reply = "本群 QQ 推送已开启：有新的首次或二次突破信号时会在群里通知。\n\n群主/管理员发送“取消订阅”可停止，发送“帮助”查看命令。";
  if (["帮助", "help", "菜单", "指令"].includes(command)) {
    reply = "可用命令：\n@机器人 订阅：开启本群信号推送\n@机器人 取消订阅：群主/管理员停止推送\n@机器人 帮助：查看本说明";
  } else if (["订阅", "subscribe", "start", "开启"].includes(command)) {
    reply = "本群 QQ 推送已开启：有新的首次或二次突破信号时会在群里通知。群主/管理员发送“取消订阅”可停止。";
  }
  await sendQQGroupText(groupOpenid, reply, requestEnv, event.id);
}

async function processQQEvent(payload, requestEnv) {
  const type = payload.t;
  const event = payload.d || {};
  const isGroupEvent = ["GROUP_AT_MESSAGE_CREATE", "GROUP_MSG_RECEIVE"].includes(type);
  const isUserEvent = ["C2C_MESSAGE_CREATE", "C2C_MSG_RECEIVE", "C2C_MSG_REJECT", "FRIEND_DEL"].includes(type);
  if (isGroupEvent && !qqGroupOpenid(event)) return;
  if (isUserEvent && !qqUserOpenid(event)) return;
  if (!isGroupEvent && !isUserEvent) return;

  // Webhook delivery is at-least-once. Claim the event before mutating the
  // subscription or replying, so a QQ retry cannot send duplicate welcomes.
  if (payload.id) {
    const claim = await env.DB.prepare(
      "INSERT OR IGNORE INTO qq_events (event_id, created_at) VALUES (?, ?)"
    ).bind(payload.id, new Date().toISOString()).run();
    if (claim.meta?.changes !== 1) return;
  }

  if (isGroupEvent) {
    await processQQGroupEvent(event, requestEnv);
    return;
  }

  const userOpenid = qqUserOpenid(event);
  const now = new Date().toISOString();
  if (type === "C2C_MSG_REJECT" || type === "FRIEND_DEL") {
    await env.DB.prepare(
      "INSERT INTO qq_subscribers (user_openid, status, created_at, updated_at) VALUES (?, 'unsubscribed', ?, ?) "
      + "ON CONFLICT (user_openid) DO UPDATE SET status = 'unsubscribed', updated_at = excluded.updated_at"
    ).bind(userOpenid, now, now).run();
    return;
  }
  if (type !== "C2C_MESSAGE_CREATE" && type !== "C2C_MSG_RECEIVE") return;

  const command = qqMessageCommand(event.content);
  if (["取消订阅", "退订", "unsubscribe", "stop", "关闭"].includes(command)) {
    await env.DB.prepare(
      "INSERT INTO qq_subscribers (user_openid, status, created_at, updated_at, last_message_at) VALUES (?, 'unsubscribed', ?, ?, ?) "
      + "ON CONFLICT (user_openid) DO UPDATE SET status = 'unsubscribed', updated_at = excluded.updated_at, last_message_at = excluded.last_message_at"
    ).bind(userOpenid, now, now, now).run();
    if (type === "C2C_MESSAGE_CREATE" && event.id) {
      await sendQQText(userOpenid, "QQ 推送已关闭。需要恢复时发送“订阅”即可。", requestEnv, event.id);
    }
    return;
  }

  // Adding the bot and sending the first private message is the opt-in. This
  // keeps the user flow simple while still making cancellation explicit.
  await upsertQQSubscriber(userOpenid, now);
  if (type !== "C2C_MESSAGE_CREATE" || !event.id) return;

  let reply = "QQ 推送已开启：有新的首次或二次突破信号时会私聊通知你。\n\n发送“取消订阅”可停止推送，发送“帮助”查看命令。";
  if (["帮助", "help", "菜单", "指令"].includes(command)) {
    reply = "可用命令：\n订阅：开启 QQ 信号推送\n取消订阅：停止 QQ 信号推送\n帮助：查看本说明";
  } else if (["订阅", "subscribe", "start", "开启"].includes(command)) {
    reply = "QQ 推送已开启：有新的首次或二次突破信号时会私聊通知你。发送“取消订阅”可停止推送。";
  }
  await sendQQText(userOpenid, reply, requestEnv, event.id);
}

async function handleQQEvent(request, requestEnv, ctx) {
  if (request.method !== "POST") return json({ error: "method not allowed" }, 405);
  if (!requestEnv.QQ_APP_ID || !requestEnv.QQ_APP_SECRET) {
    return json({ error: "QQ integration is not configured" }, 503);
  }
  const appid = request.headers.get("X-Bot-Appid") || "";
  if (appid !== requestEnv.QQ_APP_ID) return json({ error: "unauthorized" }, 401);

  const body = await request.text();
  if (body.length > 1_000_000) return json({ error: "payload too large" }, 413);
  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    return json({ error: "invalid JSON body" }, 400);
  }

  // QQ validates a new webhook with op=13. The validation response is signed
  // with the same Ed25519 key used for normal event verification.
  if (payload.op === 13) {
    const validation = payload.d || {};
    if (!validation.plain_token || !validation.event_ts) {
      return json({ error: "invalid validation payload" }, 400);
    }
    return json({
      plain_token: validation.plain_token,
      signature: await qqSign(
        requestEnv.QQ_APP_SECRET,
        `${validation.event_ts}${validation.plain_token}`
      ),
    });
  }

  const verified = await verifyQQSignature(
    requestEnv.QQ_APP_SECRET,
    request.headers.get("X-Signature-Timestamp") || "",
    request.headers.get("X-Signature-Ed25519") || "",
    body
  );
  if (!verified) return json({ error: "invalid signature" }, 401);

  if (payload.op === 0 && ctx) ctx.waitUntil(processQQEvent(payload, requestEnv));
  return json({ op: 12 });
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
      "Confirm your email to start receiving CHoCH signal emails.",
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
      "You are subscribed. An email will be sent when a new 二次突破 CHoCH signal is confirmed.",
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
  // Email intentionally remains restricted to fresh bullish second
  // breakouts. QQ has its own stream so dashboard-only first bullish
  // breakouts can be delivered without changing email rules.
  const qqInput = Array.isArray(body.qq_signals) ? body.qq_signals : signals;
  if (!signals.length && !qqInput.length) return json({ sent: true, delivered: 0 });
  // Defense in depth: the CLI applies this gate too, but the public delivery
  // boundary must not trust a malformed or replayed authenticated payload.
  const now = Date.now();
  const swingSignals = signals.filter((s) => {
    if (!s || typeof s !== "object") return false;
    const signalTime = Date.parse(s.signal_time);
    return s.layer === "swing"
      && s.direction === "bullish"
      && s.email_ok === true
      && s.notify === true
      && s.level_tag === "second"
      && s.key
      && Number.isFinite(signalTime)
      && signalTime <= now
      && now - signalTime <= SIGNAL_MAX_AGE_MS;
  });
  const qqSignals = qqInput.filter((s) => {
    if (!s || typeof s !== "object") return false;
    const signalTime = Date.parse(s.signal_time);
    return s.layer === "swing"
      && s.direction === "bullish"
      && ["first", "second"].includes(s.level_tag)
      && s.key
      && Number.isFinite(signalTime)
      && signalTime <= now
      && now - signalTime <= SIGNAL_MAX_AGE_MS;
  });
  if (!swingSignals.length && !qqSignals.length) {
    return json({ sent: true, delivered: 0, note: "internal only" });
  }

  let delivered = 0;
  let subscribers = [];
  if (swingSignals.length) {
    const result = await env.DB.prepare(
      "SELECT email FROM subscribers WHERE status = 'active'"
    ).all();
    subscribers = result.results || [];
    for (const subscriber of subscribers) {
      // Claim keys before calling Resend. This closes the race where concurrent
      // delivery requests both observe an empty deliveries row and send twice.
      const claims = swingSignals.map((s) =>
        env.DB.prepare(
          "INSERT OR IGNORE INTO deliveries (email, signal_key, created_at) VALUES (?, ?, ?)"
        ).bind(subscriber.email, s.key, new Date().toISOString())
      );
      const claimResults = await env.DB.batch(claims);
      const fresh = swingSignals.filter((_, index) => claimResults[index]?.meta?.changes === 1);
      if (!fresh.length) continue;

      const subject = `Shanzhai 信号：${fresh.length} 个新 CHoCH（${fresh.filter((s) => s.direction === "bullish").length} 涨 ${fresh.filter((s) => s.direction === "bearish").length} 跌）`;
      const unsubscribeUrl = `${env.BASE_URL}/api/unsubscribe?e=${encodeURIComponent(subscriber.email)}&t=${await unsubscribeToken(subscriber.email)}`;
      try {
        await sendEmail(subscriber.email, subject, digestText(fresh, unsubscribeUrl), digestHtml(fresh, unsubscribeUrl));
      } catch (error) {
        // Release claims only when Resend reports failure so a later scan can
        // retry. If the Worker crashes after a successful send, the claim stays
        // in D1 and prevents a duplicate on the next request.
        await env.DB.batch(fresh.map((s) =>
          env.DB.prepare("DELETE FROM deliveries WHERE email = ? AND signal_key = ?")
            .bind(subscriber.email, s.key)
        ));
        throw error;
      }
      delivered += fresh.length;
    }
  }
  const qq = qqSignals.length
    ? await deliverQQSignals(qqSignals)
    : { delivered: 0, subscribers: 0, groups: 0, failures: 0 };
  return json({
    sent: true,
    delivered,
    qq_delivered: qq.delivered,
    qq_subscribers: qq.subscribers,
    qq_groups: qq.groups,
    qq_failures: qq.failures,
    ...(qq.error ? { qq_error: qq.error } : {}),
  });
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

function qqDigestText(signals) {
  const lines = [`Shanzhai 新信号 ${signals.length} 个：`, ""];
  for (const s of signals) {
    const arrow = s.direction === "bullish" ? "▲" : "▼";
    const verb = s.direction === "bullish" ? "突破" : "跌破";
    const move = (Number(s.breakout_pct) >= 0 ? "+" : "") + Number(s.breakout_pct).toFixed(2) + "%";
    const level = s.level_tag === "second" ? "二次突破" : "首次突破";
    lines.push(`${arrow} ${s.symbol} ${verb} ${formatPrice(s.level)}（${level}），收 ${formatPrice(s.close)}（${move}）`);
    lines.push(`   时间：${beijingTime(s.signal_time)}`);
    lines.push("");
  }
  lines.push(`${env.BASE_URL}（查看图表）`);
  lines.push("仅供研究参考，不构成投资建议。");
  const text = lines.join("\n");
  if (text.length <= QQ_MAX_MESSAGE_LENGTH) return text;
  return `${text.slice(0, QQ_MAX_MESSAGE_LENGTH - 50)}\n……消息过长，其余信号请打开仪表盘查看。`;
}

async function deliverQQTargets(signals, targets, targetColumn, deliveryTable, send, label) {
  let delivered = 0;
  let failures = 0;
  for (const target of targets) {
    const targetOpenid = target[targetColumn];
    if (!targetOpenid) continue;
    const now = new Date().toISOString();
    const claims = signals.map((signal) =>
      env.DB.prepare(
        `INSERT OR IGNORE INTO ${deliveryTable} (${targetColumn}, signal_key, created_at) VALUES (?, ?, ?)`
      ).bind(targetOpenid, signal.key, now)
    );
    const claimResults = await env.DB.batch(claims);
    const fresh = signals.filter((_, index) => claimResults[index]?.meta?.changes === 1);
    if (!fresh.length) continue;

    try {
      await send(targetOpenid, qqDigestText(fresh), env);
      delivered += fresh.length;
    } catch (error) {
      failures += 1;
      console.error(`${label} delivery failed`, error);
      // A failed send must be retried on the next signal delivery request.
      await env.DB.batch(fresh.map((signal) =>
        env.DB.prepare(
          `DELETE FROM ${deliveryTable} WHERE ${targetColumn} = ? AND signal_key = ?`
        ).bind(targetOpenid, signal.key)
      ));
    }
  }
  return { delivered, failures };
}

async function deliverQQSignals(signals) {
  if (!env.QQ_APP_ID || !env.QQ_APP_SECRET) {
    return { delivered: 0, subscribers: 0, groups: 0, failures: 0, error: "QQ not configured" };
  }
  let subscribers = [];
  let groups = [];
  const lookupErrors = [];
  try {
    const result = await env.DB.prepare(
      "SELECT user_openid FROM qq_subscribers WHERE status = 'active'"
    ).all();
    subscribers = result.results || [];
  } catch (error) {
    console.error("QQ subscriber lookup failed", error);
    lookupErrors.push("subscriber lookup failed");
  }
  try {
    const result = await env.DB.prepare(
      "SELECT group_openid FROM qq_groups WHERE status = 'active'"
    ).all();
    groups = result.results || [];
  } catch (error) {
    console.error("QQ group lookup failed", error);
    lookupErrors.push("group lookup failed");
  }

  const userDelivery = await deliverQQTargets(
    signals,
    subscribers,
    "user_openid",
    "qq_deliveries",
    (userOpenid, content, requestEnv) => sendQQText(userOpenid, content, requestEnv),
    "QQ user"
  );
  const groupDelivery = await deliverQQTargets(
    signals,
    groups,
    "group_openid",
    "qq_group_deliveries",
    (groupOpenid, content, requestEnv) => sendQQGroupText(groupOpenid, content, requestEnv),
    "QQ group"
  );
  return {
    delivered: userDelivery.delivered + groupDelivery.delivered,
    subscribers: subscribers.length,
    groups: groups.length,
    failures: userDelivery.failures + groupDelivery.failures,
    ...(lookupErrors.length ? { error: lookupErrors.join(", ") } : {}),
  };
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
    const level = s.level_tag === "second" ? "二次突破" : "首次突破";
    lines.push(`${arrow} ${s.symbol} ${verb} ${formatPrice(s.level)}（${level}），收 ${formatPrice(s.close)}（${move}）`);
    lines.push(`   时间：${beijingTime(s.signal_time)}`);
    lines.push("");
  }
  lines.push(`${env.BASE_URL}（查看图表）`);
  lines.push("");
  lines.push("二次突破 = 同一币种第 2 次突破 1h 结构位。BOS = 顺势延续，CHoCH = 走势反转。");
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
      const level = s.level_tag === "second" ? "二次突破" : "首次突破";
      return `<tr>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;font-weight:600;white-space:nowrap">${escHtml(s.symbol)}</td>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;color:${color};white-space:nowrap">${arrow} ${bullish ? "看涨" : "看跌"}</td>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;white-space:nowrap">${escHtml(s.tag)}</td>
        <td style="padding:9px 12px;border-bottom:1px solid #eceff3;white-space:nowrap">${escHtml(level)}</td>
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
        <th style="text-align:left;padding:9px 12px">级别</th>
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
  async fetch(request, requestEnv, ctx) {
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
      if (request.method === "POST" && path === "/api/qq/events") {
        return await handleQQEvent(request, requestEnv, ctx);
      }
      return json({ error: "not found" }, 404);
    } catch (error) {
      console.error(error);
      return json({ error: "service unavailable" }, 502);
    }
  },
};
