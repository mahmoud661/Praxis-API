# Backend

Monorepo containing a TypeScript API gateway, a TS auth service, a Python
AI-agents service, and the local infra they need (Kafka, Redis, Postgres,
observability). Each service is self-contained so it can be extracted to
its own repo later without surgery.

Services follow **Clean Architecture** (domain → application →
infrastructure / presentation), **SOLID** at every layer, and explicit
**Dependency Injection** (tsyringe for TS, manual composition root for
Python). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the layering
rules and where to put new features.

## Architecture

```text
                          ┌──────────────────┐
   Frontend ────HTTPS────▶│     Gateway      │  (TS Express, :3000)
                          │  - cookie session│
                          │  - Redis lookup  │
                          │  - inject X-User │
                          └────────┬─────────┘
                                   │  HTTP (cookie stripped, X-User-Id added)
              ┌────────────────────┼──────────────────────┐
              ▼                                           ▼
       ┌──────────────┐                             ┌────────────────────┐
       │ auth-service │                             │ ai-agents-service  │
       │  (TS, :3001) │                             │  (Python, :8000)   │
       │  Postgres    │                             │  Postgres          │
       │  Redis (sess)│                             │                    │
       └──────┬───────┘                             └───────┬────────────┘
              │ produces                                    │ consumes
              │ auth.events.v1                              │ auth.events.v1
              └────────────────────► Kafka ◄────────────────┘
                                       │
                              ┌────────┴────────┐
                              │  OTel Collector │ ─▶ Jaeger
                              │                 │ ─▶ Prometheus ─▶ Grafana
                              └─────────────────┘
```

**Sync path** (user clicks, waits for response): Frontend → Gateway → service.
**Async path** (side effects): Service → Kafka → other service(s).

## Repo layout

```text
backend/
├── infra/                  # docker-compose, Prometheus, Grafana, OTel configs
├── gateway/                # public API gateway (TS Express)
├── services/
│   ├── auth/               # TS — signup, login, sessions
│   └── ai-agents/          # Python — agent CRUD, Kafka consumer
├── contracts/              # Kafka event envelopes + JSON Schemas
├── Makefile                # `make up` / `make down` / `make logs`
├── .env.example
└── README.md
```

Each service folder contains its own `package.json`/`pyproject.toml`,
`Dockerfile`, and `README.md` — no imports cross service boundaries.

## First-time setup (fresh clone)

Prereqs: Docker Desktop. (`make` is optional — every target maps to a
plain `docker compose` command, see the Makefile.)

```sh
cp .env.example .env
# Generate a real session secret (32+ bytes) — the auth-service refuses
# to boot with the placeholder.
#   macOS / Linux:
#     sed -i '' "s/^SESSION_SECRET=.*/SESSION_SECRET=$(openssl rand -hex 32)/" .env
#   PowerShell:
#     (Get-Content .env) -replace 'SESSION_SECRET=.*', "SESSION_SECRET=$([guid]::NewGuid().Guid + [guid]::NewGuid().Guid)" | Set-Content .env

# Bring everything up. Topics + DLQs are created by the kafka-init service,
# migrations run automatically when each app boots.
make up           # or: docker compose -f infra/docker-compose.yml --env-file .env up -d --build
```

UIs:

- Gateway (HTTPS edge via Caddy) — <https://localhost:8443> (self-signed cert; `-k` in curl or "Accept" once in the browser)
- Gateway (plain HTTP, internal)  — <http://localhost:4000>
- Kafka UI    — <http://localhost:8080>
- Grafana     — <http://localhost:3030> (admin/admin)
- Prometheus  — <http://localhost:9090>
- Jaeger      — <http://localhost:16686>
- Loki        — <http://localhost:3100> (queried via Grafana)

> **Note on git.** `backend/.env` is in `.gitignore` and must never be
> committed. The committed file is `.env.example` with safe placeholders;
> every developer creates their own `.env` locally.

## Smoke test

Use `-k` because the local Caddy cert is self-signed.

```sh
# 1. Sign up. Save cookies into cookies.txt.
curl -k -i -c cookies.txt -X POST https://localhost:8443/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"me@example.com","password":"correcthorsebattery"}'

# 2. Confirm session is live (the response includes roles).
curl -k -b cookies.txt https://localhost:8443/v1/auth/me

# 3. Create an agent. Idempotency-Key dedupes parallel retries.
curl -k -b cookies.txt -X POST https://localhost:8443/v1/agents \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{"name":"Research helper","system_prompt":"you research"}'

# 4. List agents (the ai-agents-service auto-provisions a "Default Agent"
#    by consuming the UserRegistered event the auth-service publishes).
curl -k -b cookies.txt https://localhost:8443/v1/agents
```

## Session auth model

- Auth-service writes `sess:<sid>` to Redis on login (TTL: `SESSION_TTL_SECONDS`).
- The gateway reads it on every request, attaches `req.user` (incl. roles),
  refreshes the TTL (sliding session).
- The downstream service receives `X-User-Id` / `X-User-Email` /
  `X-User-Roles`. The session cookie is stripped for non-auth hops.
- Logout = `DELETE sess:<sid>`. Cookie cleared client-side.
- Cookie is HttpOnly + signed; `Secure` only when `NODE_ENV=production`.

## Polyrepo escape hatch

The monorepo is for velocity, not coupling. To split a service into its
own repo:

1. Copy that service's folder.
2. Copy the env keys it reads (in `.env.example`).
3. Adjust its CI/CD to its own pipeline.
4. Point the gateway at the new service's URL via env.

No code change needed in any other service — services only know each
other through HTTP endpoints and Kafka topics, not imports.

## Production notes

- Auto topic creation is **off**. Manage topics explicitly (Kafka UI / CLI / IaC).
- `SESSION_SECRET` must be a real random secret (32+ bytes). Use a secrets manager.
- Grafana admin password defaults to `admin/admin` — change before exposing.
- `synchronize`-style DB migrations live inside each service; replace the
  inline DDL with a real migration tool (node-pg-migrate, alembic) before
  scaling beyond local dev.
- Each service runs as a non-root user in its container.
- Add TLS termination (Caddy/Traefik/nginx/cloud LB) in front of the gateway.
