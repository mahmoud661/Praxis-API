import type { Request, Response, NextFunction } from "express";
import type Redis from "ioredis";

// Per-identity sliding-window rate limit. Identity = user id if attached,
// IP otherwise — so authenticated users get one bucket regardless of which
// IP they come from. Uses a Redis INCR + EXPIRE for atomicity.
export function makePerIdentityRateLimit(
  redis: Redis,
  perMinute: number,
) {
  return async function rateLimit(
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const id = req.user?.id ?? req.ip ?? "anon";
      const bucket = Math.floor(Date.now() / 60_000); // minute-aligned key
      const key = `rl:${id}:${bucket}`;
      const count = await redis.incr(key);
      if (count === 1) await redis.expire(key, 60);
      res.setHeader("X-RateLimit-Limit", String(perMinute));
      res.setHeader("X-RateLimit-Remaining", String(Math.max(0, perMinute - count)));
      if (count > perMinute) {
        res.status(429).json({ error: "RATE_LIMITED" });
        return;
      }
      next();
    } catch (err) {
      // Don't kill the request if Redis hiccups — fail open. The dedicated
      // `/auth/*` rate limit (in-process) still protects credentials.
      next();
      void err;
    }
  };
}
