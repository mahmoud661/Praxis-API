import Redis from "ioredis";
import { inject, injectable } from "tsyringe";
import { Email } from "../../domain/value-objects/Email";
import { LoginAttemptTracker } from "../../domain/ports/LoginAttemptTracker";
import { ENV_TOKEN, Env } from "../config/Env";
import { REDIS_TOKEN } from "../config/tokens";

const KEY_PREFIX = "login:fail:";

// Account-lockout adapter backed by Redis (same client singleton as the
// session store). One counter key per email, INCR + EXPIRE sent as a single
// MULTI/EXEC so they reach Redis together — two separate round trips could
// be split by a client crash, leaving a counter with no TTL (= a permanent
// lock once it crosses the threshold). The TTL is re-armed on EVERY failure:
//   - the window rolls — failures only accumulate while they keep coming
//     within LOCKOUT_WINDOW_SECONDS of each other;
//   - a lock lasts the full window from the failure that engaged it;
//   - a lost EXPIRE self-heals on the next attempt;
//   - the key's TTL is the lock TTL — no separate lock key, nothing to clean.
// `isLocked` is a plain GET + compare; `reset` deletes the counter.
@injectable()
export class RedisLoginAttemptTracker implements LoginAttemptTracker {
  private readonly maxFailures: number;
  private readonly windowSeconds: number;

  constructor(
    @inject(REDIS_TOKEN) private readonly redis: Redis,
    @inject(ENV_TOKEN) env: Env,
  ) {
    this.maxFailures = env.LOCKOUT_MAX_FAILURES;
    this.windowSeconds = env.LOCKOUT_WINDOW_SECONDS;
  }

  async recordFailure(email: Email): Promise<number> {
    const key = this.key(email);
    const results = await this.redis
      .multi()
      .incr(key)
      .expire(key, this.windowSeconds)
      .exec();
    // exec() resolves null when the transaction is discarded.
    const [err, count] = results?.[0] ?? [new Error("lockout MULTI discarded"), null];
    if (err || typeof count !== "number") {
      throw err ?? new Error("lockout INCR returned a non-number");
    }
    return count;
  }

  async isLocked(email: Email): Promise<boolean> {
    const raw = await this.redis.get(this.key(email));
    return raw !== null && Number(raw) >= this.maxFailures;
  }

  async reset(email: Email): Promise<void> {
    await this.redis.del(this.key(email));
  }

  private key(email: Email): string {
    return `${KEY_PREFIX}${email.value}`;
  }
}
