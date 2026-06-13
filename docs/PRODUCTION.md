# Production runbook

Operational reference for deploying and operating the backend platform.
For the architectural overview see `docs/ARCHITECTURE.md`.

---

## Prerequisites

- Docker Engine 25+ with Compose V2 (`docker compose` plugin, not `docker-compose`).
- A secret store (Vault / Doppler / cloud secrets manager) that can materialize
  `.env.prod` on the host just before deployment (see [Secrets](#secrets)).
- DNS pointed at the host; Caddy handles TLS automatically via Let's Encrypt.

---

## First deployment

```bash
# 1. Clone the repo onto the production host.
git clone <repo-url> /opt/backend && cd /opt/backend/backend

# 2. Materialize secrets (example using Doppler):
#    doppler secrets download --format env --no-file > infra/.env.prod
#    Permissions: chmod 600 infra/.env.prod

# 3. Validate the merged compose config (dry-run, no containers started):
make prod-config

# 4. Build images and start the stack:
make up-prod

# 5. Run database migrations (auth service only — ai-agents uses Alembic-free
#    SQLAlchemy with synchronize=False in prod; schema managed by migration files
#    under services/ai-agents/alembic/ if added in future):
docker exec auth-service \
  npm run migration:run:prod

# 6. Confirm health:
curl -sf http://localhost:4000/healthz   # gateway liveness
curl -sf http://localhost:4000/readyz    # gateway + Redis
```

> **Note:** Caddy auto-provisions a Let's Encrypt cert for the hostname in
> `infra/caddy/Caddyfile`. Point your DNS A record to the host before step 4
> or set `CADDY_HTTPS_PORT=8443` temporarily to use the self-signed fallback.

---

## Rolling update

```bash
git pull
make prod-config          # validate before touching containers
make build                # or: docker compose ... build <service>
make up-prod              # recreates only changed services

# Run migrations if entities changed:
docker exec auth-service npm run migration:run:prod
```

Compose recreates only services whose image or config changed. Kafka, Postgres,
and Redis are unaffected unless their service definition changed.

---

## Auth service migrations

The auth service uses TypeORM with `synchronize: false` in production.
Every schema change must go through a migration.

### Generate a migration (after changing an entity)

```bash
# Run from services/auth/ with a throwaway Postgres that matches prod schema:
DATABASE_URL=postgres://auth:<pass>@localhost:5433/auth \
  npm run migration:generate -- migrations/<DescriptiveName>

# Verify it produces no further diff:
DATABASE_URL=... npm run migration:generate -- migrations/Verify
# The Verify file should be empty (no changes). Delete it.
```

### Apply in production

```bash
docker exec auth-service npm run migration:run:prod
# Or equivalently (runs against the container's DATABASE_URL):
docker exec auth-service \
  node -e "require('./dist/infrastructure/database/data-source').AppDataSource
            .initialize().then(ds => ds.runMigrations()).then(() => process.exit(0))"
```

### Rollback one migration

```bash
docker exec auth-service npm run migration:revert:prod
# Inspect current state:
docker exec auth-service npm run migration:show:prod
```

---

## Secrets

All secrets are declared with `${VAR:?error}` in `docker-compose.prod.yml`.
Compose hard-fails on startup if any are unset or empty — this prevents
deploying with placeholder values.

| Variable | How to generate |
|---|---|
| `SESSION_SECRET` | `openssl rand -hex 32` |
| `REDIS_PASSWORD` | `openssl rand -hex 32` |
| `AUTH_DB_PASSWORD` | `openssl rand -hex 16` (URL-safe chars only) |
| `AGENTS_DB_PASSWORD` | `openssl rand -hex 16` |
| `GRAFANA_ADMIN_PASSWORD` | `openssl rand -hex 16` |

**Rotation procedure for `SESSION_SECRET`:**

1. Generate a new secret: `openssl rand -hex 32`.
2. Keep the old value alongside the new one in a temporary two-secret setup
   (cookie-parser's `secret` can be an array; sessions signed with any entry
   are valid). Update `.env.prod` with both.
3. Deploy. All existing sessions remain valid.
4. After the session TTL (`SESSION_TTL_SECONDS`, default 24h) elapses — or
   after forcing a logout of all users — remove the old secret.
5. Deploy again with only the new secret.

---

## Postgres backup restore drill

Run quarterly to verify backups are usable.

```bash
# List available dumps (auth example):
docker exec postgres-auth-backup ls /backups/

# Restore to a temporary database for verification:
docker run --rm \
  --network backend_platform \
  -v postgres_auth_backups:/backups:ro \
  postgres:16-alpine \
  sh -c 'gunzip -c /backups/last/auth-latest.sql.gz | \
         pg_restore -h postgres-auth -U auth -d auth_restore --format=custom'

# If using custom format directly:
docker run --rm \
  --network backend_platform \
  -v postgres_auth_backups:/backups:ro \
  postgres:16-alpine \
  pg_restore -h postgres-auth -U auth -d auth_restore \
    --format=custom /backups/last/auth-latest.dump
```

The backup sidecar stores dumps under `/backups/last/`, `/backups/daily/`,
`/backups/weekly/`, and `/backups/monthly/`. Retention: 7 days / 4 weeks / 6 months.

---

## Kafka HA (3-broker prod cluster)

The prod overlay runs `kafka`, `kafka-2`, and `kafka-3` in a KRaft quorum.
Topics are created with `replication-factor=3` and `min.insync.replicas=2`,
so the cluster tolerates one broker failure without message loss or downtime.

**Check cluster health:**

```bash
docker exec kafka \
  /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server kafka:9092 \
    --describe
```

**Broker failure recovery:**

1. If one broker is down, Kafka continues with ISR=2. Producers and consumers
   are unaffected.
2. Restart the failed broker: `docker compose ... restart kafka-2`.
3. The broker rejoins the ISR automatically after catching up.

If all three brokers are down, no new messages can be produced or consumed.
Restore from the most recent volume snapshot and restart all three together:

```bash
make down-prod
make up-prod
```

---

## Adding a new service

1. Create `services/<name>/` with its own `Dockerfile`, `package.json` /
   `pyproject.toml`, and tests.
2. Add the service to `infra/docker-compose.yml` (base) and
   `infra/docker-compose.prod.yml` (prod overrides: no host ports, required
   secrets).
3. Register any new Kafka topics in `contracts/topics.json` with schema files;
   CI's `contracts` job will validate them.
4. Add a test job to `.github/workflows/ci.yml` under the parallel unit block
   and add the job name to the `ci-success` gate's `needs` list.

---

## Observability

In production all service ports except Caddy (80/443) are unexposed.
Access dashboards via an SSH tunnel into the docker network:

```bash
# Grafana (port 3000 inside the network):
ssh -L 3030:localhost:3030 <host>
# Then open http://localhost:3030

# Prometheus:
ssh -L 9090:localhost:9090 <host>
```

Alerts fire to the Alertmanager webhook configured in
`infra/alertmanager/alertmanager.yml`. Replace the placeholder receiver
with Slack, PagerDuty, or your on-call tool before going live.
