# auth-service

Owns user accounts, login, signup, and sessions. Built with Clean
Architecture + SOLID, DI via [tsyringe](https://github.com/microsoft/tsyringe).

## Layout

```text
src/
├── domain/                      pure, framework-free
│   ├── entities/User.ts
│   ├── value-objects/           Email, PasswordHash, UserId
│   ├── events/UserRegisteredEvent.ts
│   ├── shared/                  Result, AggregateRoot, DomainEvent, exceptions
│   └── ports/                   UserRepository, PasswordHasher, SessionStore,
│                                EventPublisher, UnitOfWork, Logger  (interfaces)
│
├── application/
│   ├── UseCase.ts               marker interface
│   ├── dtos.ts                  application boundary types
│   └── use-cases/               SignUp, LogIn, LogOut, GetCurrentUser
│
├── infrastructure/              implements every port above
│   ├── persistence/             Postgres connection, mapper, repo, UoW
│   ├── security/                BcryptPasswordHasher
│   ├── sessions/                RedisSessionStore
│   ├── messaging/               KafkaEventPublisher
│   ├── logging/                 PinoLoggerAdapter
│   └── config/Env.ts            zod-validated env
│
├── presentation/http/           controllers (Auth, Health), error mw, cookies,
│                                ExpressAppFactory, resultMapper
│
├── composition-root.ts          wires concrete adapters to port tokens
└── main.ts                      boot + graceful shutdown
```

## Endpoints

| Method | Path           | Auth | Use case                |
| ------ | -------------- | ---- | ----------------------- |
| POST   | `/auth/signup` | none | `SignUpUseCase`         |
| POST   | `/auth/login`  | none | `LogInUseCase`          |
| POST   | `/auth/logout` | sess | `LogOutUseCase`         |
| GET    | `/auth/me`     | sess | `GetCurrentUserUseCase` |
| GET    | `/healthz`     | none | liveness                |
| GET    | `/readyz`      | none | DB + Redis reachable    |

## How DI flows

1. `main.ts` calls `buildContainer()`.
2. `composition-root.ts` instantiates concrete adapters and registers them
   against port tokens (`USER_REPOSITORY`, `EVENT_PUBLISHER`, …).
3. `container.resolve(ExpressAppFactory)` walks the dependency graph:
   factory → controllers → use cases → ports → adapters.
4. The domain and application layers only saw port interfaces. No
   adapter import ever crossed into them.

## Sessions

- Cookie: `sid`, HttpOnly, SameSite=Lax, signed.
- Storage: Redis key `sess:<id>` → JSON `{ userId, email, createdAt }`.
- TTL: `SESSION_TTL_SECONDS`. Sliding — refreshed on every `GET /auth/me`.

## Events emitted

- Topic `auth.events.v1` → `UserRegistered` after successful signup.

## Local dev

```sh
cp ../../.env.example .env
npm install
npm run start:dev
```

## Polyrepo extraction

Self-contained — no imports from sibling services. Copy this folder
plus the env keys it reads to its own repo and it builds standalone.
