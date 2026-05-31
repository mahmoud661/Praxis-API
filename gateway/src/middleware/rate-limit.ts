import rateLimit from "express-rate-limit";
import RedisStore, { type RedisReply } from "rate-limit-redis";
import type Redis from "ioredis";

// Per-identity rate limit. Identity = user id when attached, IP otherwise,
// so authenticated users get one bucket regardless of which IP they come
// from. Backed by Redis (`rate-limit-redis`) so the count is shared across
// gateway instances behind a load balancer.
//
// Built on `express-rate-limit` rather than a hand-rolled INCR + EXPIRE
// loop: same Redis semantics, plus CodeQL's `js/missing-rate-limiting`
// recognises the factory call. Headers (`RateLimit`, `RateLimit-Remaining`)
// follow the IETF draft via `standardHeaders: 'draft-7'`.
export function makePerIdentityRateLimit(
  redis: Redis,
  perMinute: number,
): ReturnType<typeof rateLimit> {
  return rateLimit({
    windowMs: 60_000,
    limit: perMinute,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    // express-rate-limit feeds the key to the store; the user id when
    // present, otherwise the source IP, otherwise a literal "anon"
    // bucket (every unauthenticated request without an IP shares it —
    // unusual in practice, but the bucket has to exist).
    keyGenerator: (req) => req.user?.id ?? req.ip ?? "anon",
    handler: (_req, res) => {
      res.status(429).json({ error: "RATE_LIMITED" });
    },
    store: new RedisStore({
      // `sendCommand` is the abstract bridge to whichever client we
      // use. ioredis `call()` takes the raw command + args and
      // returns the same shape rate-limit-redis expects.
      sendCommand: (...args: string[]) =>
        redis.call(args[0]!, ...args.slice(1)) as Promise<RedisReply>,
      // Distinct prefix from sessions / idempotency so the keyspace
      // is greppable in `redis-cli`.
      prefix: "rl:",
    }),
  });
}
