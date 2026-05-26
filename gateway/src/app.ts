import express from "express";
import helmet from "helmet";
import cors from "cors";
import cookieParser from "cookie-parser";
import rateLimit from "express-rate-limit";
import Redis from "ioredis";

import { GatewayConfig } from "./config";
import { Logger } from "./ports/Logger";
import { SessionResolver } from "./ports/SessionResolver";
import {
  makeAttachSession,
  requireSession,
} from "./middleware/session.middleware";
import { makeServiceProxy } from "./middleware/proxy";
import { makePerIdentityRateLimit } from "./middleware/rate-limit";
import { makeIdempotency } from "./middleware/idempotency";
import {
  CircuitBreaker,
  makeCircuitBreakerMiddleware,
} from "./middleware/circuit-breaker";

export function buildApp(deps: {
  config: GatewayConfig;
  resolver: SessionResolver;
  redis: Redis;
  logger: Logger;
}): express.Express {
  const { config, resolver, redis, logger } = deps;
  const app = express();

  app.disable("x-powered-by");
  // Trust one hop (the LB or Caddy in front). `true` is too permissive and
  // trips express-rate-limit's validation.
  app.set("trust proxy", 1);

  app.use(helmet());

  // Multi-origin CORS allow-list. Reflects the request's Origin when it
  // matches; otherwise blocks. Credentials enabled so cookies cross.
  app.use(
    cors({
      origin: (origin, cb) => {
        if (!origin || config.frontendOrigins.includes(origin)) {
          cb(null, origin ?? false);
        } else {
          cb(new Error(`CORS: origin ${origin} not allowed`));
        }
      },
      credentials: true,
    }),
  );

  app.use(express.json({ limit: "100kb" }));
  app.use(cookieParser(config.SESSION_SECRET));

  app.get("/healthz", (_req, res) => res.json({ status: "ok" }));
  app.get("/readyz", async (_req, res) => {
    try {
      await redis.ping();
      res.json({ ready: true });
    } catch (err) {
      res.status(503).json({ ready: false, error: (err as Error).message });
    }
  });

  const attachSession = makeAttachSession(resolver, config.SESSION_COOKIE_NAME);
  const perIdentityLimit = makePerIdentityRateLimit(redis, config.RATE_LIMIT_PER_MINUTE);
  const idempotency = makeIdempotency(redis);

  // Per-upstream circuit breakers — one bad downstream doesn't trip another.
  const authBreaker = new CircuitBreaker(
    "auth-service",
    config.PROXY_CB_THRESHOLD,
    config.PROXY_CB_RESET_MS,
  );
  const agentsBreaker = new CircuitBreaker(
    "ai-agents-service",
    config.PROXY_CB_THRESHOLD,
    config.PROXY_CB_RESET_MS,
  );

  // Tight in-process limit for auth surface (credential-stuffing defense).
  const authLimiter = rateLimit({
    windowMs: 60_000,
    limit: 20,
    standardHeaders: true,
    legacyHeaders: false,
  });

  // Re-add the upstream base path that Express stripped via `app.use(mount, ...)`.
  // Don't produce a trailing slash for bare-mount: FastAPI would 307 to the
  // canonical path with the internal hostname in the Location header.
  const reprefix = (base: string) => (path: string) =>
    path === "/" || path === "" ? base : `${base}${path}`;

  // -----------------------------------------------------------------
  // Versioned API under /v1. Future v2 mounts alongside without breaking v1.
  // -----------------------------------------------------------------

  // /v1/auth/* — auth-service owns the cookie; forwardCookie:true.
  app.use(
    "/v1/auth",
    authLimiter,
    attachSession,
    makeCircuitBreakerMiddleware(authBreaker),
    makeServiceProxy({
      target: config.AUTH_SERVICE_URL,
      requireAuth: false,
      forwardCookie: true,
      pathRewrite: reprefix("/auth"),
    }),
  );

  // /v1/agents/* — requires session; downstream sees X-User-Id, no cookie.
  app.use(
    "/v1/agents",
    attachSession,
    requireSession,
    perIdentityLimit,
    idempotency,
    makeCircuitBreakerMiddleware(agentsBreaker),
    makeServiceProxy({
      target: config.AI_AGENTS_SERVICE_URL,
      requireAuth: true,
      pathRewrite: reprefix("/agents"),
    }),
  );

  // -----------------------------------------------------------------
  // Legacy paths (no version prefix) — kept as alias for backward compat,
  // marked Deprecated. Remove once all clients move to /v1.
  // -----------------------------------------------------------------
  const deprecate = (newPath: string) =>
    (_req: express.Request, res: express.Response, next: express.NextFunction) => {
      res.setHeader("Deprecation", "true");
      res.setHeader("Link", `<${newPath}>; rel="successor-version"`);
      next();
    };

  app.use(
    "/auth",
    deprecate("/v1/auth"),
    authLimiter,
    attachSession,
    makeCircuitBreakerMiddleware(authBreaker),
    makeServiceProxy({
      target: config.AUTH_SERVICE_URL,
      requireAuth: false,
      forwardCookie: true,
      pathRewrite: reprefix("/auth"),
    }),
  );
  app.use(
    "/api/agents",
    deprecate("/v1/agents"),
    attachSession,
    requireSession,
    perIdentityLimit,
    idempotency,
    makeCircuitBreakerMiddleware(agentsBreaker),
    makeServiceProxy({
      target: config.AI_AGENTS_SERVICE_URL,
      requireAuth: true,
      pathRewrite: reprefix("/agents"),
    }),
  );

  app.use((_req, res) => res.status(404).json({ error: "NOT_FOUND" }));

  logger.info("gateway routes wired", {
    versions: ["v1"],
    breakers: [authBreaker.name, agentsBreaker.name],
  });
  return app;
}
