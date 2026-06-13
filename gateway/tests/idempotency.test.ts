import { describe, expect, it, vi } from "vitest";
import type { Request, Response } from "express";
import type Redis from "ioredis";
import { makeIdempotency } from "../src/middleware/idempotency";

// Minimal stub of the ioredis surface used by makeIdempotency, with
// real SET ... NX semantics so the in-flight lock behaves like Redis.
function makeFakeRedis() {
  const store = new Map<string, string>();
  return {
    store,
    get: vi.fn(async (k: string) => store.get(k) ?? null),
    set: vi.fn(async (k: string, v: string, ...args: unknown[]) => {
      if (args.includes("NX") && store.has(k)) return null;
      store.set(k, v);
      return "OK";
    }),
    del: vi.fn(async (k: string) => (store.delete(k) ? 1 : 0)),
  };
}

function fakeReq(overrides: Record<string, unknown> = {}): Request {
  const headers = (overrides.headers as Record<string, string> | undefined) ?? {};
  return {
    method: "POST",
    path: "/agents/run",
    user: { id: "u-1", email: "a@b.co", roles: ["user"] },
    body: { prompt: "hello" },
    header: (name: string) => headers[name],
    ...overrides,
  } as unknown as Request;
}

function fakeRes() {
  const handlers: Record<string, () => void> = {};
  const stub = {
    statusCode: 200,
    headers: {} as Record<string, unknown>,
    status: vi.fn(),
    json: vi.fn().mockReturnThis(),
    send: vi.fn().mockReturnThis(),
    setHeader: vi.fn(),
    getHeader: vi.fn(),
    on: vi.fn(),
    emitFinish: () => handlers["finish"]?.(),
  };
  stub.status.mockImplementation((code: number) => {
    stub.statusCode = code;
    return stub;
  });
  stub.setHeader.mockImplementation((k: string, v: unknown) => {
    stub.headers[k.toLowerCase()] = v;
    return stub;
  });
  stub.getHeader.mockImplementation((k: string) => stub.headers[k.toLowerCase()]);
  stub.on.mockImplementation((event: string, cb: () => void) => {
    handlers[event] = cb;
    return stub;
  });
  return stub as unknown as typeof stub & Response;
}

// Let the fire-and-forget redis writes inside the middleware settle.
const flush = () => new Promise((r) => setImmediate(r));

describe("idempotency middleware", () => {
  it("passes through when no Idempotency-Key header is present", async () => {
    const redis = makeFakeRedis();
    const mw = makeIdempotency(redis as unknown as Redis);
    const next = vi.fn();
    await mw(fakeReq(), fakeRes(), next);
    expect(next).toHaveBeenCalledOnce();
    expect(redis.get).not.toHaveBeenCalled();
  });

  it("ignores non-mutating methods even when a key is sent", async () => {
    const redis = makeFakeRedis();
    const mw = makeIdempotency(redis as unknown as Redis);
    const next = vi.fn();
    await mw(
      fakeReq({ method: "GET", headers: { "Idempotency-Key": "k-1" } }),
      fakeRes(),
      next,
    );
    expect(next).toHaveBeenCalledOnce();
    expect(redis.get).not.toHaveBeenCalled();
  });

  it("lets the first request through and marks the key pending", async () => {
    const redis = makeFakeRedis();
    const mw = makeIdempotency(redis as unknown as Redis);
    const next = vi.fn();
    await mw(fakeReq({ headers: { "Idempotency-Key": "k-1" } }), fakeRes(), next);
    expect(next).toHaveBeenCalledOnce();
    const pendingKeys = [...redis.store.entries()].filter(([, v]) => v === "pending");
    expect(pendingKeys).toHaveLength(1);
    expect(pendingKeys[0][0]).toContain("idem:u-1:POST:/agents/run:k-1:");
  });

  it("replays the cached response on a duplicate key + identical body", async () => {
    const redis = makeFakeRedis();
    const mw = makeIdempotency(redis as unknown as Redis);

    // First request: goes through, then "responds" via res.send → cached.
    const res1 = fakeRes();
    await mw(fakeReq({ headers: { "Idempotency-Key": "k-1" } }), res1, vi.fn());
    res1.statusCode = 201;
    res1.send('{"id":"row-1"}');
    await flush();

    // Retry with the same key + body: replayed, never reaches next().
    const res2 = fakeRes();
    const next2 = vi.fn();
    await mw(fakeReq({ headers: { "Idempotency-Key": "k-1" } }), res2, next2);
    expect(next2).not.toHaveBeenCalled();
    expect(res2.status).toHaveBeenCalledWith(201);
    expect(res2.setHeader).toHaveBeenCalledWith("Idempotent-Replay", "true");
    expect(res2.send).toHaveBeenCalledWith('{"id":"row-1"}');
  });

  it("treats the same key with a DIFFERENT body as a fresh request (no replay)", async () => {
    const redis = makeFakeRedis();
    const mw = makeIdempotency(redis as unknown as Redis);

    const res1 = fakeRes();
    await mw(fakeReq({ headers: { "Idempotency-Key": "k-1" } }), res1, vi.fn());
    res1.send('{"id":"row-1"}');
    await flush();

    // Same key, different payload → different body hash → cache miss.
    const res2 = fakeRes();
    const next2 = vi.fn();
    await mw(
      fakeReq({ headers: { "Idempotency-Key": "k-1" }, body: { prompt: "DIFFERENT" } }),
      res2,
      next2,
    );
    expect(next2).toHaveBeenCalledOnce();
    expect(res2.setHeader).not.toHaveBeenCalledWith("Idempotent-Replay", "true");
  });

  it("returns 409 while the first request with the key is still in flight", async () => {
    const redis = makeFakeRedis();
    const mw = makeIdempotency(redis as unknown as Redis);

    // First request acquires the pending lock and hasn't responded yet.
    await mw(fakeReq({ headers: { "Idempotency-Key": "k-1" } }), fakeRes(), vi.fn());

    const res2 = fakeRes();
    const next2 = vi.fn();
    await mw(fakeReq({ headers: { "Idempotency-Key": "k-1" } }), res2, next2);
    expect(next2).not.toHaveBeenCalled();
    expect(res2.status).toHaveBeenCalledWith(409);
    expect(res2.json).toHaveBeenCalledWith({ error: "IDEMPOTENCY_IN_FLIGHT" });
  });

  it("returns 409 when SET NX loses the race after the initial GET miss", async () => {
    const redis = makeFakeRedis();
    // GET says the key is free, but by the time we SET NX someone else won.
    redis.get = vi.fn(async () => null);
    redis.set = vi.fn(async () => null);
    const mw = makeIdempotency(redis as unknown as Redis);
    const res = fakeRes();
    const next = vi.fn();
    await mw(fakeReq({ headers: { "Idempotency-Key": "k-1" } }), res, next);
    expect(next).not.toHaveBeenCalled();
    expect(res.status).toHaveBeenCalledWith(409);
  });

  it("releases a still-pending lock on finish (proxied responses)", async () => {
    const redis = makeFakeRedis();
    const mw = makeIdempotency(redis as unknown as Redis);
    const res = fakeRes();
    await mw(fakeReq({ headers: { "Idempotency-Key": "k-1" } }), res, vi.fn());

    // Proxy wrote via res.write/end, so res.send never cached anything.
    res.emitFinish();
    await flush();
    expect([...redis.store.values()]).not.toContain("pending");

    // A retry is now a fresh first request rather than a stuck 409.
    const res2 = fakeRes();
    const next2 = vi.fn();
    await mw(fakeReq({ headers: { "Idempotency-Key": "k-1" } }), res2, next2);
    expect(next2).toHaveBeenCalledOnce();
  });
});
