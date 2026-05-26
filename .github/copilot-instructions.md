# Instructions for AI assistants

Read this file before making changes to the backend codebase. It
captures the conventions that aren't enforced by the linter but break
review if missed.

## Critical rules

1. **Domain layer imports nothing infrastructure.** No `pg`, `kafkajs`,
   `express`, `asyncpg`, `aiokafka`, `pydantic` (FastAPI's pydantic
   models live in `presentation/`) in `domain/`. Only port interfaces.
2. **Never edit an existing migration.** Add a new one. Both
   `node-pg-migrate` (auth) and `alembic` (ai-agents) ship with a
   migration-version table that rejects already-applied files mutated
   in place.
3. **Use cases return `Result`, not exceptions.** `Result.ok(...)` /
   `Result.fail(err)`. Exceptions are reserved for *infra* errors that
   should bubble (DB unreachable, etc.). The presentation layer maps
   `Result.fail` to HTTP status via `resultMapper` (TS) / `result_mapper.py` (Py).
4. **Cookies live in the gateway and auth-service. Nowhere else.**
   Downstream services receive `X-User-Id` / `X-User-Email` /
   `X-User-Roles` from the gateway. They never re-read the cookie.
5. **Events are emitted via the EventPublisher port.** In auth that
   port is bound to the **transactional outbox** adapter — `publish`
   writes to the `outbox` table inside the current transaction. A
   separate `OutboxPoller` ships rows to Kafka. Do not call kafkajs
   directly from a use case.
6. **`.env` is gitignored.** Real secrets only live in `.env`.
   `.env.example` carries placeholders. If you add a new env var,
   update `.env.example` in the same PR.

## Layered conventions

```
domain/         pure: entities, value objects, events, ports
application/    use cases — depend on ports, return Result
infrastructure/ adapters — implement the ports (Postgres, Redis, Kafka, …)
presentation/   controllers (HTTP / Kafka dispatchers) — translate transport
composition-root.ts / composition_root.py — the only place that knows the concrete adapters
```

When adding a feature:

1. Model the new concept in `domain/` (entity / value object / event).
2. Declare new ports if needed.
3. Write a use case in `application/use-cases/` (or `use_cases/`).
4. Implement the new ports in `infrastructure/`.
5. Expose the use case through `presentation/`.
6. Wire it in the composition root.

If you find yourself importing infrastructure into a use case, stop and
re-read step 2.

## Testing

- Unit tests live next to the layer they test, with in-memory fakes for
  every port (`tests/helpers/fakes.ts` / `tests/helpers/fakes.py`).
- Unit tests run **inside the Dockerfile** so a failing test aborts the
  image. Don't disable that gate.
- Integration tests use testcontainers (real Postgres). They live in
  `tests-integration/`. They're a separate CI job — they don't block
  the image.

## Things that should NEVER appear in a PR

- A controller calling a repository directly.
- A use case calling `new Pool({...})` / `asyncpg.create_pool` / `new Kafka(...)`.
- `auto-create topics: true` reintroduced in compose.
- A new test file marked `.skip` / `xfail` without a comment + tracking
  issue + remove-by date.
- A new dependency added to `package.json` / `requirements.txt` without
  a one-line note in the PR explaining what justifies it.
- Direct usage of `process.env` or `os.environ` outside `infrastructure/config/`.

## Naming + style

- Use cases: `<Verb><Noun>UseCase.ts` / `<verb_noun>_use_case.py` ⇒ one
  public method `execute`.
- Ports: `<Capability>.ts` (interface only) / `<capability>.py`
  (Protocol). The DI token lives next to the interface, named
  `SCREAMING_CASE`.
- Adapters: `<Concrete><Capability>.ts` ⇒
  `PostgresUserRepository`, `BcryptPasswordHasher`,
  `RedisSessionStore`, `OutboxEventPublisher`.

## When you're not sure

Ask. The cost of a clarifying question is small; the cost of guessing
wrong about whether a use case should own a transaction is large.
