# Architecture

## Layered design (Clean Architecture)

Each business service (`auth`, `ai-agents`) is split into four layers,
arranged so that dependencies always point **inward** toward the domain:

```
   ┌───────────────────────────────────────────────────────────────┐
   │                    Presentation                               │
   │       (controllers, HTTP / Kafka adapters, DTOs)              │
   └───────────────────────────┬───────────────────────────────────┘
                               │ depends on
                               ▼
   ┌───────────────────────────────────────────────────────────────┐
   │                    Application                                │
   │       (use cases, application DTOs, Result type)              │
   └───────────────────────────┬───────────────────────────────────┘
                               │ depends on
                               ▼
   ┌───────────────────────────────────────────────────────────────┐
   │                       Domain                                  │
   │  Entities • Aggregates • Value Objects • Domain Events        │
   │  Ports (interfaces): Repository, EventPublisher, Hasher, …    │
   └───────────────────────────────────────────────────────────────┘
                               ▲ implements
                               │
   ┌───────────────────────────┴───────────────────────────────────┐
   │                  Infrastructure                               │
   │  Postgres adapters • Redis • Kafka • bcrypt • Pino / structlog│
   └───────────────────────────────────────────────────────────────┘
```

- **Domain** has zero external imports. No `express`, no `pg`, no `kafkajs`.
- **Application** depends only on the domain (entities + ports). Use cases
  hold the business workflow; they ask ports to do work but never know
  which adapter actually does it.
- **Infrastructure** *implements* the ports declared in the domain. This
  is the Dependency Inversion Principle: the high-level policy (domain)
  defines the interface, the low-level detail (infrastructure) conforms.
- **Presentation** is the outermost adapter — HTTP routes and Kafka
  consumers — that translates between transport-shaped data and DTOs.

The **Composition Root** (one file per service: `composition-root.ts`
or `composition_root.py`) is the only place that imports concrete
adapters *and* port tokens together. It wires everything and hands the
assembled graph to `main`.

## SOLID, applied

- **Single Responsibility:** each use case (`SignUpUseCase`,
  `LogInUseCase`, `CreateAgentUseCase`) has one reason to change.
  Repositories handle persistence and nothing else. Controllers translate
  transport — they don't validate business rules.
- **Open / Closed:** add a new auth flow (magic-link, OAuth) by adding
  a new use case + new ports + new adapters. None of the existing
  use cases or domain code is touched.
- **Liskov Substitution:** any `UserRepository` implementation is
  interchangeable — Postgres in prod, in-memory in tests. Same for
  `PasswordHasher`, `SessionStore`, `EventPublisher`.
- **Interface Segregation:** ports are small and role-specific
  (`PasswordHasher` has two methods; we don't lump everything into one
  `SecurityService`). A consumer only sees the methods it actually uses.
- **Dependency Inversion:** use cases depend on port *interfaces* declared
  in the domain. Infrastructure adapters depend on those same interfaces.
  Both halves point at the abstraction — neither knows the other concrete.

## Dependency Injection

| Service              | DI mechanism                                                    |
| -------------------- | --------------------------------------------------------------- |
| `auth` (TS)          | [tsyringe](https://github.com/microsoft/tsyringe) — `@injectable`, `@inject(TOKEN)`. Tokens are `Symbol`s declared next to the port. |
| `ai-agents` (Python) | Manual constructor injection. The composition root builds use cases by hand; FastAPI `Depends()` exposes them to controllers. |
| `gateway` (TS)       | Manual constructor injection (small surface, edge component). Middleware is a factory that takes the resolver as a parameter. |

Both styles serve the same goal: **business code never instantiates its
own dependencies**. They are passed in.

## Why not NestJS

NestJS has its own DI and Module system. It works, but locks domain code
to NestJS-specific decorators (`@Injectable()`, `@Module()`). With
tsyringe + Express, the domain stays decorator-free; we only use
`@injectable()` in the application + presentation layers, and the
infrastructure adapters use the container indirectly via the composition
root. Easier to extract to another framework later, easier to test.

## Where to add a new feature

1. Model the new concept in **domain/** (entity, value object, event).
2. Declare any new ports (`domain/ports/`) you'll need.
3. Write a use case in **application/use-cases/** depending on those ports.
4. Implement the new ports in **infrastructure/** (Postgres adapter,
   Kafka adapter, …).
5. Expose the use case via **presentation/** (HTTP route or Kafka handler).
6. Register adapters in **composition-root**.

Steps 1–3 require no infrastructure decisions. Steps 4–5 don't require
domain changes. That's the payoff of the layering.
