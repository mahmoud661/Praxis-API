# ai-agents-service

Owns AI agents (definitions, runs). Built with Clean Architecture + SOLID;
DI via manual constructor injection from a composition root.

## Layout

```text
app/
├── domain/                       pure, framework-free
│   ├── entities/agent.py
│   ├── value_objects/            AgentId, OwnerId, AgentName
│   ├── shared/                   Result, AggregateRoot, DomainEvent, exceptions
│   └── ports/                    AgentRepository, EventPublisher,
│                                 EventConsumer, Logger  (Protocols)
│
├── application/
│   ├── dtos.py                   application boundary types
│   └── use_cases/                CreateAgent, ListUserAgents,
│                                 ProvisionDefaultAgent
│
├── infrastructure/               implements every port above
│   ├── persistence/              asyncpg connection, mapper, repo, migrations
│   ├── messaging/                KafkaEventPublisher, KafkaEventConsumer
│   ├── logging/                  StructlogLogger
│   └── config/env.py             pydantic-settings env
│
├── presentation/http/            controllers, dependencies, result_mapper
│
├── composition_root.py           builds the dependency graph by hand
└── main.py                       FastAPI app factory + lifespan
```

## Endpoints

| Method | Path       | Auth    | Use case                |
| ------ | ---------- | ------- | ----------------------- |
| GET    | `/agents`  | session | `ListUserAgentsUseCase` |
| POST   | `/agents`  | session | `CreateAgentUseCase`    |
| GET    | `/healthz` | none    | liveness                |
| GET    | `/readyz`  | none    | DB reachable            |

`session` means the gateway forwarded `X-User-Id` after verifying the
session. This service trusts that header — it is not reachable from
outside the docker network.

## Events consumed

- Topic `auth.events.v1` → `UserRegistered` triggers
  `ProvisionDefaultAgentUseCase` which creates a default agent for the
  new user.

## How DI flows

1. `main.create_app()` calls `build_container()`.
2. The composition root constructs adapters (`PostgresAgentRepository`,
   `KafkaEventPublisher`, `KafkaEventConsumer`, `StructlogLogger`) and
   passes them into use cases via constructors.
3. Controllers receive use cases via `make_router(create=…, list_for_user=…)`
   — this is constructor injection at the router level, since FastAPI
   doesn't have a separate "controller class" concept.
4. The lifespan hook starts/stops infra (DB pool, Kafka consumer/producer).

The domain and application code never import asyncpg / aiokafka /
structlog directly — only the Protocols in `domain/ports/`.

## Local dev (uv)

```sh
cp ../../.env.example .env
uv sync                     # creates .venv from pyproject.toml
uv run uvicorn app.main:app --reload --port 8000
```

Or run inside the container stack:

```sh
docker compose -f ../../infra/docker-compose.yml --env-file ../../.env up -d --build ai-agents-service
```

Needs Postgres + Kafka reachable per `.env`.

## Polyrepo extraction

Self-contained. To split out: copy `services/ai-agents/` + the env keys
this service uses; build context becomes `.`.
