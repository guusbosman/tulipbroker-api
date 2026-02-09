-- YugabyteDB schema for TulipBroker orders (YSQL)
CREATE DATABASE IF NOT EXISTS tulipbroker;
\c tulipbroker

CREATE TABLE IF NOT EXISTS orders (
  order_id UUID PRIMARY KEY,
  client_id TEXT NOT NULL,
  idempotency_key_hash TEXT NOT NULL,
  user_id TEXT NOT NULL,
  side TEXT NOT NULL,
  price NUMERIC NOT NULL,
  quantity NUMERIC NOT NULL,
  time_in_force TEXT NOT NULL,
  status TEXT NOT NULL,
  accepted_at TIMESTAMPTZ NOT NULL,
  region TEXT,
  accepted_az TEXT,
  processing_ms INTEGER,
  market TEXT,
  env TEXT,
  version TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS orders_idempotency_uq
  ON orders (client_id, idempotency_key_hash);

CREATE INDEX IF NOT EXISTS orders_recent_idx
  ON orders (accepted_at DESC);
