-- Shanzhai subscription and delivery state. Apply with:
--   wrangler d1 execute shanzhai --file schema.sql
CREATE TABLE IF NOT EXISTS subscribers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending', -- pending | active | unsubscribed
  token_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  confirmed_at TEXT,
  unsubscribed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_subscribers_status ON subscribers (status);

CREATE TABLE IF NOT EXISTS subscription_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ip TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_ip_time ON subscription_attempts (ip, created_at);

CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL,
  signal_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (email, signal_key)
);

-- QQ Bot C2C subscribers. user_openid is scoped to this AppID and is not a
-- user's QQ number; it is obtained from the verified private-message event.
CREATE TABLE IF NOT EXISTS qq_subscribers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_openid TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active', -- active | unsubscribed
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_message_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_qq_subscribers_status ON qq_subscribers (status);

CREATE TABLE IF NOT EXISTS qq_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_openid TEXT NOT NULL,
  signal_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (user_openid, signal_key)
);

-- Webhook delivery is at-least-once; this prevents duplicate replies when QQ
-- retries the same event.
CREATE TABLE IF NOT EXISTS qq_events (
  event_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);

-- QQ group subscriptions. group_openid is scoped to this AppID and is
-- obtained from GROUP_AT_MESSAGE_CREATE; it is not the numeric QQ group ID.
CREATE TABLE IF NOT EXISTS qq_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_openid TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active', -- active | unsubscribed
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_message_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_qq_groups_status ON qq_groups (status);

CREATE TABLE IF NOT EXISTS qq_group_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_openid TEXT NOT NULL,
  signal_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (group_openid, signal_key)
);
