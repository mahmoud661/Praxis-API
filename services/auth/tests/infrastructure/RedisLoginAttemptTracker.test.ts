import { beforeEach, describe, expect, it } from "vitest";
import "reflect-metadata";
import Redis from "ioredis";
import { RedisLoginAttemptTracker } from "../../src/infrastructure/security/RedisLoginAttemptTracker";
import { Email } from "../../src/domain/value-objects/Email";
import { Env } from "../../src/infrastructure/config/Env";

// Hand-rolled stub of the ioredis commands the adapter uses (plus a minimal
// MULTI/EXEC), with a manually-advanced clock so TTL expiry is tested
// without real waiting. (No ioredis-mock in the dependency tree.)
class FakeRedis {
  now = 0; // ms — advance manually to simulate time passing
  readonly store = new Map<string, { value: string; expiresAt: number | null }>();

  async incr(key: string): Promise<number> {
    const next = Number(this.live(key)?.value ?? "0") + 1;
    // INCR preserves an existing TTL; a fresh key has no TTL until EXPIRE.
    const expiresAt = this.live(key)?.expiresAt ?? null;
    this.store.set(key, { value: String(next), expiresAt });
    return next;
  }

  async expire(key: string, seconds: number): Promise<number> {
    const entry = this.live(key);
    if (!entry) return 0;
    entry.expiresAt = this.now + seconds * 1000;
    return 1;
  }

  async get(key: string): Promise<string | null> {
    return this.live(key)?.value ?? null;
  }

  async del(key: string): Promise<number> {
    return this.store.delete(key) ? 1 : 0;
  }

  // Queues commands and runs them in order on exec(), mirroring ioredis's
  // `multi().incr().expire().exec()` → Array<[error, result]> shape.
  multi() {
    const ops: Array<() => Promise<unknown>> = [];
    const chain = {
      incr: (key: string) => {
        ops.push(() => this.incr(key));
        return chain;
      },
      expire: (key: string, seconds: number) => {
        ops.push(() => this.expire(key, seconds));
        return chain;
      },
      exec: async (): Promise<Array<[Error | null, unknown]>> => {
        const out: Array<[Error | null, unknown]> = [];
        for (const op of ops) out.push([null, await op()]);
        return out;
      },
    };
    return chain;
  }

  private live(key: string) {
    const entry = this.store.get(key);
    if (!entry) return undefined;
    if (entry.expiresAt !== null && entry.expiresAt <= this.now) {
      this.store.delete(key);
      return undefined;
    }
    return entry;
  }
}

const WINDOW_SECONDS = 900; // 15 minutes
const MAX_FAILURES = 5;

function makeEnv(): Env {
  return {
    LOCKOUT_MAX_FAILURES: MAX_FAILURES,
    LOCKOUT_WINDOW_SECONDS: WINDOW_SECONDS,
  } as Env; // adapter only reads the two lockout fields
}

const alice = Email.create("alice@example.com");

describe("RedisLoginAttemptTracker", () => {
  let redis: FakeRedis;
  let tracker: RedisLoginAttemptTracker;

  beforeEach(() => {
    redis = new FakeRedis();
    tracker = new RedisLoginAttemptTracker(
      redis as unknown as Redis,
      makeEnv(),
    );
  });

  it("counts failures per email and reports the running total", async () => {
    expect(await tracker.recordFailure(alice)).toBe(1);
    expect(await tracker.recordFailure(alice)).toBe(2);
    expect(
      await tracker.recordFailure(Email.create("bob@example.com")),
    ).toBe(1); // independent counter
  });

  it("is not locked below the threshold", async () => {
    for (let i = 0; i < MAX_FAILURES - 1; i++) {
      await tracker.recordFailure(alice);
    }
    expect(await tracker.isLocked(alice)).toBe(false);
  });

  it("locks at the threshold", async () => {
    for (let i = 0; i < MAX_FAILURES; i++) await tracker.recordFailure(alice);
    expect(await tracker.isLocked(alice)).toBe(true);
  });

  it("is never locked for an email with no failures", async () => {
    expect(await tracker.isLocked(alice)).toBe(false);
  });

  it("failures expire after the window — stale attempts don't accumulate", async () => {
    await tracker.recordFailure(alice);
    await tracker.recordFailure(alice);

    redis.now += (WINDOW_SECONDS + 1) * 1000; // window elapses

    expect(await tracker.isLocked(alice)).toBe(false);
    expect(await tracker.recordFailure(alice)).toBe(1); // counter restarted
  });

  it("re-arms the TTL on every failure — the window rolls while attempts keep coming", async () => {
    await tracker.recordFailure(alice); // t=0, TTL → t=900s
    redis.now += (WINDOW_SECONDS - 100) * 1000; // t=800s, still alive

    // This failure re-arms the TTL to t=1700s; under the old fixed window
    // the key would have expired at t=900s.
    expect(await tracker.recordFailure(alice)).toBe(2);
    redis.now += (WINDOW_SECONDS - 100) * 1000; // t=1600s

    expect(await tracker.recordFailure(alice)).toBe(3); // counter survived
  });

  it("the lock lasts the FULL window from the failure that engaged it", async () => {
    // 4 failures, then most of the window passes...
    for (let i = 0; i < MAX_FAILURES - 1; i++) await tracker.recordFailure(alice);
    redis.now += (WINDOW_SECONDS - 10) * 1000;

    // ...the 5th failure trips the lock and re-arms the TTL.
    await tracker.recordFailure(alice);
    expect(await tracker.isLocked(alice)).toBe(true);

    // Still locked where the ORIGINAL window would have ended.
    redis.now += 60 * 1000;
    expect(await tracker.isLocked(alice)).toBe(true);

    // Unlocked once the full window since the lock engaged has elapsed.
    redis.now += WINDOW_SECONDS * 1000;
    expect(await tracker.isLocked(alice)).toBe(false);
  });

  it("reset clears the counter immediately, even when locked", async () => {
    for (let i = 0; i < MAX_FAILURES; i++) await tracker.recordFailure(alice);
    expect(await tracker.isLocked(alice)).toBe(true);

    await tracker.reset(alice);

    expect(await tracker.isLocked(alice)).toBe(false);
    expect(await tracker.recordFailure(alice)).toBe(1);
  });

  it("keys failures by normalized email under the login:fail: prefix", async () => {
    await tracker.recordFailure(Email.create("  ALICE@Example.COM "));
    expect(redis.store.has("login:fail:alice@example.com")).toBe(true);
    // Same account regardless of input casing.
    expect(await tracker.recordFailure(alice)).toBe(2);
  });
});
