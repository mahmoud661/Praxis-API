# gateway

Single public entry point. Verifies the session against Redis, attaches
the user, and proxies to the right internal service.

The gateway is an **edge component** — its job is routing and session
resolution, not business logic. So it keeps a lighter structure than the
business services: ports + adapters + manual constructor injection,
without a full domain/application split (there is no domain to model).

## Layout

```text
src/
├── ports/                    SessionResolver, Logger  (interfaces)
├── adapters/                 RedisSessionResolver, PinoLogger
├── middleware/
│   ├── session.middleware.ts attaches req.user via the injected resolver
│   └── proxy.ts              strips cookie, injects X-User-Id, forwards
├── config.ts                 zod-validated env
├── app.ts                    buildApp(deps): assembles Express from ports
└── main.ts                   composition root + boot + shutdown
```

## Routes

| Public path     | Auth    | Forwards to                          |
| --------------- | ------- | ------------------------------------ |
| `/auth/*`       | none    | `auth-service` (signup, login, etc.) |
| `/api/agents/*` | session | `ai-agents-service` (`/agents/*`)    |
| `/healthz`      | none    | local liveness                       |
| `/readyz`       | none    | local readiness (Redis reachable)    |

## Session flow

1. Frontend hits `/auth/login` → cookie is set by `auth-service` (the
   gateway just forwards; it doesn't inspect set-cookies).
2. Frontend includes the cookie on subsequent requests.
3. `attachSession` middleware (port `SessionResolver`) looks up
   `sess:<sid>` in Redis, attaches `req.user`, refreshes the TTL.
4. `requireSession` blocks unauthenticated traffic on protected routes.
5. The proxy strips the cookie and injects `X-User-Id` + `X-User-Email`
   so the downstream service never sees the session cookie.

## Why a custom gateway (not Traefik / Kong)

- Sharing the session key shape with `auth-service` is trivial when both
  speak TypeScript.
- Custom code gives us one place to add per-user rate limits, request
  enrichment, A/B routing, response shaping — without writing config in
  some plugin DSL.

## Local dev

```sh
cp ../.env.example .env
npm install
npm run start:dev
```
