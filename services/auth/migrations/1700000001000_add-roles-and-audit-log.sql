-- Up Migration
-- All statements idempotent so re-running on an already-migrated DB is safe.

-- RBAC: roles live on the user row.
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS roles TEXT[] NOT NULL DEFAULT ARRAY['user'];

CREATE INDEX IF NOT EXISTS users_roles_gin_idx ON users USING gin (roles);

-- Append-only audit log of security-relevant events.
CREATE TABLE IF NOT EXISTS audit_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor_id    UUID NULL,
  action      VARCHAR(80) NOT NULL,
  target_id   VARCHAR(120) NULL,
  details     JSONB NOT NULL DEFAULT '{}'::jsonb,
  ip          INET NULL
);
CREATE INDEX IF NOT EXISTS audit_log_occurred_at_idx ON audit_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_actor_idx ON audit_log (actor_id);
CREATE INDEX IF NOT EXISTS audit_log_action_idx ON audit_log (action);

-- Transactional outbox: events written atomically with the business row,
-- shipped to Kafka by the OutboxPoller. No lost events on crash.
CREATE TABLE IF NOT EXISTS outbox (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aggregate_id  VARCHAR(120) NOT NULL,
  topic         VARCHAR(120) NOT NULL,
  event_name    VARCHAR(120) NOT NULL,
  payload       JSONB NOT NULL,
  headers       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at  TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS outbox_pending_idx ON outbox (created_at) WHERE published_at IS NULL;

-- Down Migration

DROP TABLE IF EXISTS outbox;
DROP TABLE IF EXISTS audit_log;
DROP INDEX IF EXISTS users_roles_gin_idx;
ALTER TABLE users DROP COLUMN IF EXISTS roles;
