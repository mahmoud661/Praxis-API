-- Up Migration
-- Idempotent baseline so this is safe whether the DB is empty or already
-- has the schema from the previous boot-time-DDL approach.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
  id            UUID PRIMARY KEY,
  email         VARCHAR(320) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS users_email_idx ON users (email);

-- Down Migration

DROP TABLE IF EXISTS users;
