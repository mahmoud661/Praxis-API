// OpenTelemetry is loaded via NODE_OPTIONS=--require ... before this file
// even runs (see docker-compose.yml). No import here on purpose.
import "dotenv/config";
import { parse as parseCookie } from "cookie";
import cookieParser from "cookie-parser";
import Redis from "ioredis";
import { loadConfig } from "./config";
import { PinoLogger } from "./adapters/PinoLogger";
import { RedisSessionResolver } from "./adapters/RedisSessionResolver";
import { buildApp } from "./app";
import type { IncomingMessage } from "http";
import type { Duplex } from "stream";
import type { SessionResolver } from "./ports/SessionResolver";

const SHUTDOWN_DEADLINE_MS = 15_000;

function main(): void {
  const config = loadConfig();
  const logger = new PinoLogger(
    config.serviceName,
    config.NODE_ENV === "production" ? "info" : "debug",
  );
  const redis = new Redis(config.REDIS_URL, { maxRetriesPerRequest: 3 });
  redis.on("error", (err) => logger.error("redis error", { err: err.message }));

  const resolver = new RedisSessionResolver(redis, config.SESSION_TTL_SECONDS);

  const { app, wsProxy, sandboxWsProxy } = buildApp({ config, resolver, redis, logger });
  const server = app.listen(config.PORT, () => {
    logger.info("gateway listening", { port: config.PORT });
  });

  // WebSocket upgrades. Runs BEFORE the proxy forwards so we can do session
  // auth here (cookie-parser middleware doesn't fire on `upgrade` events).
  // On success: inject X-User-Id headers (same shape as HTTP) and call the
  // proxy's `.upgrade`. On failure: close the raw socket with 401.
  server.on("upgrade", (req, socket, head) => {
    void handleUpgrade(req, socket, head);
  });

  async function handleUpgrade(
    req: IncomingMessage,
    socket: Duplex,
    head: Buffer,
  ): Promise<void> {
    try {
      if (!req.url?.startsWith("/v1/ws/")) {
        rejectUpgrade(socket, 404);
        return;
      }
      const session = await resolveSession(req, config, resolver);
      if (!session) {
        rejectUpgrade(socket, 401);
        return;
      }
      // Strip any client-supplied identity headers first — the assignments
      // below only overwrite unconditionally for x-user-id, so a forged
      // x-user-email/x-user-roles would otherwise survive when the session
      // lacks those fields. Same defense-in-depth as the HTTP proxy.
      delete req.headers["x-user-id"];
      delete req.headers["x-user-email"];
      delete req.headers["x-user-roles"];
      // Inject identity headers — same contract as the HTTP proxy
      // (downstream `X-User-Id` etc.) so ws_authenticate on ai-agents sees them.
      req.headers["x-user-id"] = session.userId;
      if (session.email) req.headers["x-user-email"] = session.email;
      if (session.roles) req.headers["x-user-roles"] = session.roles.join(",");
      // Cookies were used for auth; drop them before forwarding so the
      // downstream can't accidentally use them.
      delete req.headers.cookie;

      // `socket` is a Duplex per Node's `IncomingMessage.upgrade` type, but
      // at runtime it's always a net.Socket — that's what the HTTP server
      // hands us. http-proxy-middleware's `.upgrade` is typed `socket: Socket`
      // so we narrow.
      const netSocket = socket as import("net").Socket;
      // Route to the right upstream: sandbox stream vs ai-agents chat/notif.
      // Both proxies live under /v1/ws/*; the path prefix disambiguates.
      if (req.url.startsWith("/v1/ws/sandbox")) {
        sandboxWsProxy.upgrade?.(req, netSocket, head);
      } else {
        wsProxy.upgrade?.(req, netSocket, head);
      }
    } catch (err) {
      logger.error("ws.upgrade.failed", {
        err: err instanceof Error ? err.message : String(err),
      });
      rejectUpgrade(socket, 500);
    }
  }

  let shuttingDown = false;
  const shutdown = async (signal: string): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info("shutting down", { signal });

    // Drain in-flight requests up to the deadline before exit.
    await new Promise<void>((resolve) => {
      const deadline = setTimeout(() => {
        logger.warn("shutdown drain timed out; forcing close");
        resolve();
      }, SHUTDOWN_DEADLINE_MS);
      server.close(() => {
        clearTimeout(deadline);
        resolve();
      });
    });

    await redis.quit().catch(() => undefined);
    process.exit(0);
  };

  process.on("SIGTERM", () => void shutdown("SIGTERM"));
  process.on("SIGINT", () => void shutdown("SIGINT"));
}

// -----------------------------------------------------------------------
// Helpers — session resolution on a raw HTTP upgrade request.
// -----------------------------------------------------------------------

interface AuthedSession {
  userId: string;
  email?: string;
  roles?: string[];
}

async function resolveSession(
  req: IncomingMessage,
  config: ReturnType<typeof loadConfig>,
  resolver: SessionResolver,
): Promise<AuthedSession | null> {
  const cookieHeader = req.headers.cookie ?? "";
  if (!cookieHeader) return null;

  const cookies = parseCookie(cookieHeader);
  const signed = cookies[config.SESSION_COOKIE_NAME];
  if (!signed) return null;

  // cookie-parser stamps a `s:` prefix when signing. `signedCookie` checks
  // it and returns the unsigned value or false.
  const unsigned = cookieParser.signedCookie(signed, config.SESSION_SECRET);
  if (!unsigned) return null;

  const session = await resolver.resolve(unsigned);
  if (!session) return null;
  return {
    userId: session.id,
    email: session.email,
    roles: session.roles,
  };
}

function rejectUpgrade(socket: Duplex, status: number): void {
  const reason =
    status === 401
      ? "Unauthorized"
      : status === 404
      ? "Not Found"
      : "Server Error";
  socket.write(`HTTP/1.1 ${status} ${reason}\r\nConnection: close\r\n\r\n`);
  socket.destroy();
}

main();
