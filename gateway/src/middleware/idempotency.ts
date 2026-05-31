import type { Request, Response, NextFunction } from "express";
import type Redis from "ioredis";
import { createHash } from "crypto";

// Idempotency middleware (RFC draft "Idempotency-Key" header pattern).
// On the FIRST request with a given key for a given user+method+path, we
// stash a "pending" marker in Redis and let the request through. On
// REPEATED calls with the same key:
//   - if we have a cached response, replay it (same status + body)
//   - otherwise return 409 (another request with this key is in flight)
//
// Only applies to mutating methods (POST/PUT/PATCH/DELETE). GET ignored.

const TTL_SECONDS = 24 * 60 * 60;
const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

interface CachedResponse {
  status: number;
  body: string;
  contentType: string;
}

function bodyHash(req: Request): string {
  // Hash a stable serialization of the JSON body so a typo'd retry doesn't
  // get the original's response.
  const raw = typeof req.body === "object" ? JSON.stringify(req.body) : String(req.body ?? "");
  return createHash("sha256").update(raw).digest("hex").slice(0, 16);
}

export function makeIdempotency(redis: Redis) {
  return async function idempotency(
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    if (!MUTATING.has(req.method)) return next();
    const key = req.header("Idempotency-Key");
    if (!key) return next();

    const userId = req.user?.id ?? "anon";
    const redisKey = `idem:${userId}:${req.method}:${req.path}:${key}:${bodyHash(req)}`;

    const existing = await redis.get(redisKey);
    if (existing === "pending") {
      res.status(409).json({ error: "IDEMPOTENCY_IN_FLIGHT" });
      return;
    }
    if (existing) {
      const cached = JSON.parse(existing) as CachedResponse;
      res.status(cached.status);
      res.setHeader("Content-Type", cached.contentType);
      res.setHeader("Idempotent-Replay", "true");
      res.send(cached.body);
      return;
    }

    // Mark as in-flight. NX = only set if not present (prevents two parallel
    // requests both thinking they're first).
    const acquired = await redis.set(redisKey, "pending", "EX", TTL_SECONDS, "NX");
    if (acquired !== "OK") {
      res.status(409).json({ error: "IDEMPOTENCY_IN_FLIGHT" });
      return;
    }

    // Wrap res.send so responses produced INSIDE the gateway (e.g. 400 from
    // validation) get cached for replay. This does NOT fire for proxied
    // responses — http-proxy-middleware writes via res.write/end — so for
    // those we rely on the finish handler below.
    const origSend = res.send.bind(res);
    res.send = (body: unknown): Response => {
      const contentType = res.getHeader("content-type")?.toString() ?? "application/json";
      const cached: CachedResponse = {
        status: res.statusCode,
        body: typeof body === "string" ? body : JSON.stringify(body),
        contentType,
      };
      void redis.set(redisKey, JSON.stringify(cached), "EX", TTL_SECONDS);
      return origSend(body);
    };

    // For proxied responses (where the wrap above doesn't fire), release the
    // "pending" lock when the response completes so a subsequent retry isn't
    // stuck on 409 forever. Strict body-replay for proxied responses requires
    // a proxy `responseInterceptor` — known limitation (docs/DEFERRED.md).
    res.on("finish", () => {
      void redis.get(redisKey).then((v) => {
        if (v === "pending") return redis.del(redisKey);
        return undefined;
      });
    });

    next();
  };
}
