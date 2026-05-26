# Contributing

Short version: open a PR. CI builds your branch, runs unit tests inside
each service's Docker image, and a maintainer reviews. The full setup
lives in [`backend/README.md`](../backend/README.md).

## Repo layout (the bits a contributor touches)

```
backend/
├── gateway/                       TS Express gateway (single public entry)
├── services/
│   ├── auth/                      TS — login, signup, sessions
│   └── ai-agents/                 Python — AI agents
├── infra/                         docker-compose + Caddy + Prometheus + Grafana + Loki + OTel
├── contracts/                     Kafka topic + event JSON Schemas
└── docs/                          ARCHITECTURE, DEFERRED
```

Each service is self-contained — same shape (domain → application →
infrastructure → presentation), same DI pattern, same Dockerfile with
unit tests run during the build. See
[`backend/docs/ARCHITECTURE.md`](../backend/docs/ARCHITECTURE.md).

## Local loop

```sh
cd backend
cp .env.example .env
# Generate a real SESSION_SECRET (32+ hex chars) before booting
make up
```

The gateway answers at `https://localhost:8443` (Caddy self-signed) and
`http://localhost:4000` (plain). Grafana on `:3030`, Jaeger on `:16686`.

Run unit tests outside the build, fast feedback:

```sh
# TS services
cd backend/services/auth && npm test
cd backend/gateway && npm test

# Python service
cd backend/services/ai-agents && pytest
```

Run integration tests (testcontainers spins real Postgres):

```sh
cd backend/services/auth && npm run test:integration
```

## What CI runs

A single workflow (`.github/workflows/ci.yml`) orchestrates four
reusable workflows:

| Reusable | What it does |
|---|---|
| `_lint.yml` | Per-service lint (eslint / ruff). Skips quietly if a service hasn't defined a lint config yet. |
| `_test.yml` | Builds each service's image. Tests run *inside* the Dockerfile (`RUN npm test` / `RUN pytest`), so a green image is proof tests passed. |
| `_build.yml` | On `main`, also pushes to GHCR. |
| `_integration.yml` | Heavier testcontainers job (real Postgres). Doesn't gate the image. |

A `compose-config` job validates `docker-compose.yml` parses. A final
`ci-success` aggregate job is what branch protection should depend on —
renaming a matrix entry doesn't break the rule.

## Style + rules

- **No business logic in controllers.** Translate HTTP → DTO → use case → HTTP. See `presentation/http/` in any service.
- **Domain depends on nothing.** No `pg`, no `kafkajs`, no `express` imports in `domain/`. Only port interfaces.
- **New Kafka events**: add a JSON Schema under `backend/contracts/schemas/<topic>/<EventName>.json` and update `contracts/topics.json`.
- **DB schema changes**: add a new migration file (`node-pg-migrate` for auth, `alembic` for ai-agents). Never edit an existing migration.
- **Sensitive values**: `.env` is gitignored. `.env.example` carries safe placeholders only.

## PR checklist

The PR template lists what we look for. The bare minimum:

1. Tests added/updated. They run inside the image build, so a green CI
   means the image already passes them.
2. If you added a Kafka event, the contract is in `contracts/`.
3. If you changed a schema, you wrote a migration.
4. If you added an env var, you updated `.env.example`.
5. The smoke test from `backend/README.md` still works.

## Owners

`.github/OWNERS` maps paths to reviewers. The PR welcome bot reads it
and auto-requests review from anyone with `# auto-request` on the
matching rule. You'll see the request appear automatically on a fresh PR.
