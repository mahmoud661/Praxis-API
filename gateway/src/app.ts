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
import { makeServiceProxy, makeWsProxy } from "./middleware/proxy";
import { makePerIdentityRateLimit } from "./middleware/rate-limit";
import { makeIdempotency } from "./middleware/idempotency";
import {
  CircuitBreaker,
  makeCircuitBreakerMiddleware,
} from "./middleware/circuit-breaker";

// Global error handler — Express recognises it by the 4-arg signature.
// Logs the real error via the Logger port; clients get a generic body so
// `err.message` (stack frames, internal hostnames) never leaks. Exported
// as a factory so tests can exercise it directly (no supertest dep).
export function makeErrorHandler(logger: Logger): express.ErrorRequestHandler {
  return (err, req, res, next) => {
    logger.error("unhandled error", {
      method: req.method,
      path: req.path,
      err: err instanceof Error ? err.message : String(err),
    });
    // If headers already went out, the default handler must finish the
    // response — we can't write a JSON body on top of a half-sent reply.
    if (res.headersSent) {
      next(err);
      return;
    }
    res.status(500).json({ error: "INTERNAL" });
  };
}

export interface GatewayHandles {
  app: express.Express;
  /** WebSocket proxy for `/v1/ws/*` → ai-agents-service `/ws/*`. main.ts
   *  attaches its `.upgrade` to the HTTP server's `upgrade` event after
   *  the session auth runs in that handler. */
  wsProxy: ReturnType<typeof makeWsProxy>;
}

export function buildApp(deps: {
  config: GatewayConfig;
  resolver: SessionResolver;
  redis: Redis;
  logger: Logger;
}): GatewayHandles {
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
      logger,
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
      logger,
    }),
  );

  // /v1/threads/* — same upstream (ai-agents). Conversation CRUD + history.
  app.use(
    "/v1/threads",
    attachSession,
    requireSession,
    perIdentityLimit,
    idempotency,
    makeCircuitBreakerMiddleware(agentsBreaker),
    makeServiceProxy({
      target: config.AI_AGENTS_SERVICE_URL,
      requireAuth: true,
      pathRewrite: reprefix("/threads"),
      logger,
    }),
  );

  // /v1/capabilities — same upstream. Returns the agent catalog +
  // per-account state the frontend uses to gate the composer (file
  // upload, tool toggles) at chat boot. Read-only and cheap on the
  // upstream side (LiteLLM /model/info is cached 5 min), so no
  // idempotency layer — just session + rate-limit + circuit-breaker.
  app.use(
    "/v1/capabilities",
    attachSession,
    requireSession,
    perIdentityLimit,
    makeCircuitBreakerMiddleware(agentsBreaker),
    makeServiceProxy({
      target: config.AI_AGENTS_SERVICE_URL,
      requireAuth: true,
      pathRewrite: reprefix("/capabilities"),
      logger,
    }),
  );

  // /v1/files — multipart upload for chat attachments. Same upstream.
  // Idempotency LAYERED IN: a flaky network can retry a multipart
  // upload and we'd otherwise create duplicate file rows. Per-identity
  // rate limit keeps a single user from saturating storage.
  app.use(
    "/v1/files",
    attachSession,
    requireSession,
    perIdentityLimit,
    idempotency,
    makeCircuitBreakerMiddleware(agentsBreaker),
    makeServiceProxy({
      target: config.AI_AGENTS_SERVICE_URL,
      requireAuth: true,
      pathRewrite: reprefix("/files"),
      logger,
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
      logger,
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
      logger,
    }),
  );

  // WebSocket proxy: /v1/ws/agents/{tid}, /v1/ws/notifications → ai-agents
  // /ws/agents/{tid}, /ws/notifications. Session auth happens in main.ts's
  // server.on('upgrade') handler BEFORE this proxy sees the request, so it
  // can reject unauthenticated upgrades cleanly. The HTTP-level middleware
  // below is a 426 fallback for clients that mistakenly hit /v1/ws/* without
  // an Upgrade header.
  const wsProxy = makeWsProxy({
    target: config.AI_AGENTS_SERVICE_URL,
    pathRewrite: { "^/v1/ws": "/ws" },
  });
  app.use("/v1/ws", (req, res) => {
    res
      .status(426)
      .set("Upgrade", "websocket")
      .json({ error: "UPGRADE_REQUIRED", path: req.path });
  });

  app.use((_req, res) => res.status(404).json({ error: "NOT_FOUND" }));

  // Last in the chain: anything that called next(err) lands here.
  app.use(makeErrorHandler(logger));

  logger.info("gateway routes wired", {
    versions: ["v1"],
    breakers: [authBreaker.name, agentsBreaker.name],
    ws: ["/v1/ws/*"],
  });
  return { app, wsProxy };
}
