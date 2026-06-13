# Deferred infrastructure

Items that need external systems (cloud accounts, secret stores, paid
products) or are large enough to be their own milestone. Documented here so
they aren't forgotten and so anyone touching the relevant code knows what
the production answer should look like.

---

## 1. Secrets management

**Status:** plain `.env` file.

**Risks:** `SESSION_SECRET`, DB passwords, future API keys all live in a
file that ends up on disk, in compose env, and in process memory. Any host
compromise or accidental commit leaks them.

**Production answer:**

- Use a real secrets store: **HashiCorp Vault**, **AWS/GCP/Azure Secrets
  Manager**, or **Doppler**.
- Bake the secret reference into the deployment manifest, not the env file.
- For Docker Compose v2 you can use the `secrets:` top-level key + mount
  secrets as files; for Kubernetes use `ExternalSecret` or sealed-secrets.
- Rotate `SESSION_SECRET` regularly: keep two valid secrets (current +
  previous) during the rotation window so existing sessions don't all
  invalidate at once. Requires multi-secret support in cookie-parser.

**Why deferred:** no cloud account or Vault instance to wire up.

---

## 2. Database backups

**Status: DONE (2026-06-13)** — `infra/docker-compose.prod.yml` ships a
`prodrigestivill/postgres-backup-local:16` sidecar for each Postgres
instance. Daily `pg_dump --format=custom`, 7-day/4-week/6-month rotation,
stored in named volumes `postgres_auth_backups` / `postgres_agents_backups`.
Restore drill documented in `docs/PRODUCTION.md`.

**Remaining gap:** for truly durable backups the volumes should be snapshotted
to S3/GCS (e.g. via Restic + a cron job on the host). The current setup
protects against accidental row deletion but not full-host failure.

---

## 3. Container registry

**Status:** images built locally; CI workflow has the GHCR push step
behind `if: github.ref == 'refs/heads/main'`. So images don't get
published until you actually run the workflow with the right token.

**Production answer:**

- Push to **GHCR** (already wired in `.github/workflows/backend-ci.yml`)
  or **ECR/GAR/ACR** based on cloud.
- Tag with semantic version + git sha. Workflow already does
  `sha-<short>` and `latest`.
- Scan images with `trivy` or `grype` in CI before promotion.

**Why deferred:** requires the repo to be hosted on GitHub and the
`packages: write` permission to be enabled for the actor.

---

## 4. Kafka schema registry (Avro/Protobuf)

**Status:** topics carry JSON envelopes. Producer schema and consumer
schema are coordinated via `contracts/` (topics.json + JSON Schema files).
CI now runs `scripts/validate-contracts.mjs` (the `contracts` job), which
enforces that every listed event has a structurally sane schema file —
but that's a *static* check on the contract files. There's still no
runtime enforcement: nothing stops a producer from publishing a payload
that doesn't match its schema.

**Risks:** producer changes a field name silently; consumer parses it as
`undefined` and writes broken data downstream. The CI gate catches
missing/orphaned/malformed schemas, not payload drift at runtime.

**Production answer:**

- Run **Karapace** or **Confluent Schema Registry** as another container.
- Switch producer/consumer to Avro or Protobuf and register schemas.
- Add a CI gate: a schema change PR must pass the registry's
  backward-compatibility check (`BACKWARD_TRANSITIVE` is the usual mode).

**Why deferred:** non-trivial migration — every event class needs a
schema file and a code-generation step (`avro-tools` for Java/TS,
`protoc` for proto). Worth its own dedicated effort.

---

## 5. Email + password reset

**Status:** no email infrastructure. Signup just creates a verified
account, and there's no password reset endpoint.

**Production answer:**

- Local dev: **MailHog** / **Mailpit** container (catches outbound mail
  in a UI).
- Prod: **SES**, **Postmark**, or **Resend** with verified domain.
- Add `email_verified_at` to `users`, a `password_reset_tokens` table
  (token, user_id, expires_at, used_at), and three endpoints:
  `POST /auth/verify`, `POST /auth/forgot-password`,
  `POST /auth/reset-password`.
- Verification/reset emails sent through the same `EventPublisher` port
  via a new topic `notification.commands.v1` consumed by a future
  notification service.

**Why deferred:** scope is "build new service, new tables, new flow,
and new UI" — a multi-day item.

---

## 6. Multi-broker Kafka + replication

**Status: DONE (2026-06-13)** — `infra/docker-compose.prod.yml` adds
`kafka-2` and `kafka-3` brokers (3-node KRaft quorum), overrides
`kafka` to drop its external listener, and sets
`KAFKA_DEFAULT_REPLICATION_FACTOR=3` / `KAFKA_MIN_INSYNC_REPLICAS=2`.
`kafka-init` creates topics with `rf=3 / min.insync.replicas=2` in prod.

**Remaining gap:** for a production SLA, migrate Kafka out of Compose to a
managed service (MSK, Confluent Cloud) or a Kubernetes-based operator
(Strimzi). The 3-broker compose setup is HA within a single host —
it does not survive host failure.

---

## 7. Per-tenant + per-feature flags

**Status:** no feature-flag system. New behavior ships behind code only.

**Production answer:** **GrowthBook** / **LaunchDarkly** / **Unleash**.
Self-hosted Unleash is the simplest free option.

**Why deferred:** product surface is small enough today that flags would
be infrastructure-for-infrastructure-s-sake.

---

## 8. Idempotency-key body replay (for proxied responses)

**Status:** the gateway's idempotency middleware prevents PARALLEL retries
of the same `Idempotency-Key` (returns 409 while in-flight) and caches
**in-process** responses (e.g. 400/401 from middleware) for replay. For
**proxied** responses it only releases the lock on completion; a serial
retry hits the upstream again instead of replaying the original body.

**Production answer:** wire `responseInterceptor` from
http-proxy-middleware on `makeServiceProxy` to capture the streamed
upstream body, then store it under the idempotency key. Strict body-replay
then works for proxied responses too.

**Why deferred:** non-trivial — requires `selfHandleResponse: true` and
careful handling of streaming, content-length, and content-encoding. Worth
doing as part of an "idempotency-by-default" milestone.
