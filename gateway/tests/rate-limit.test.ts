import { describe, expect, it, vi } from "vitest";
import type Redis from "ioredis";

// Capture the options handed to express-rate-limit so we can exercise the
// keyGenerator / handler directly — the real factory hides them inside the
// returned middleware.
type RateLimitOptions = {
  keyGenerator: (req: Record<string, unknown>) => string;
  limit: number;
  windowMs: number;
  handler: (req: unknown, res: unknown) => void;
};

const captured = vi.hoisted<{ options: RateLimitOptions | undefined }>(() => ({
  options: undefined,
}));

vi.mock("express-rate-limit", () => ({
  default: vi.fn((opts: unknown) => {
    captured.options = opts as RateLimitOptions;
    return (_req: unknown, _res: unknown, next: () => void) => next();
  }),
}));
vi.mock("rate-limit-redis", () => ({
  default: class {
    constructor(readonly opts: unknown) {}
  },
}));

import { makePerIdentityRateLimit } from "../src/middleware/rate-limit";

function buildAndCapture(): RateLimitOptions {
  makePerIdentityRateLimit({} as unknown as Redis, 120);
  return captured.options!;
}

describe("makePerIdentityRateLimit", () => {
  it("keys on the user id when a session is attached", () => {
    const opts = buildAndCapture();
    const key = opts.keyGenerator({
      user: { id: "u-1", email: "a@b.co", roles: ["user"] },
      ip: "203.0.113.9",
    });
    expect(key).toBe("u-1");
  });

  it("falls back to the source IP when unauthenticated", () => {
    const opts = buildAndCapture();
    expect(opts.keyGenerator({ ip: "203.0.113.9" })).toBe("203.0.113.9");
  });

  it("falls back to the shared 'anon' bucket without user or IP", () => {
    const opts = buildAndCapture();
    expect(opts.keyGenerator({})).toBe("anon");
  });

  it("configures the per-minute limit it was given", () => {
    const opts = buildAndCapture();
    expect(opts.limit).toBe(120);
    expect(opts.windowMs).toBe(60_000);
  });

  it("responds 429 RATE_LIMITED when the limit is exceeded", () => {
    const opts = buildAndCapture();
    const res = {
      status: vi.fn().mockReturnThis(),
      json: vi.fn().mockReturnThis(),
    };
    opts.handler({}, res);
    expect(res.status).toHaveBeenCalledWith(429);
    expect(res.json).toHaveBeenCalledWith({ error: "RATE_LIMITED" });
  });
});
