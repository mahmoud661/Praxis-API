import { describe, expect, it, vi } from "vitest";
import { RedisSessionResolver } from "../src/adapters/RedisSessionResolver";

type FakeRedis = {
  getex: ReturnType<typeof vi.fn>;
  set: (k: string, v: string) => void;
};

// Minimal stub of the ioredis surface used by RedisSessionResolver.
function makeFakeRedis(initial: Record<string, string> = {}): FakeRedis {
  const store = new Map<string, string>(Object.entries(initial));
  return {
    getex: vi.fn(async (k: string, _mode: string, _ttl: number) => store.get(k) ?? null),
    set: (k: string, v: string) => store.set(k, v),
  };
}

describe("RedisSessionResolver", () => {
  it("returns null for an empty session id without hitting Redis", async () => {
    const redis = makeFakeRedis();
    const resolver = new RedisSessionResolver(redis, 60);
    expect(await resolver.resolve("")).toBeNull();
    expect(redis.getex).not.toHaveBeenCalled();
  });

  it("returns null when the key is missing", async () => {
    const redis = makeFakeRedis();
    const resolver = new RedisSessionResolver(redis, 60);
    expect(await resolver.resolve("abc")).toBeNull();
    expect(redis.getex).toHaveBeenCalledWith("sess:abc", "EX", 60);
  });

  it("returns the user (with roles) and refreshes TTL on hit (sliding session)", async () => {
    const redis = makeFakeRedis({
      "sess:abc": JSON.stringify({
        userId: "u-1",
        email: "a@b.co",
        roles: ["user", "admin"],
        createdAt: "2024-01-01T00:00:00Z",
      }),
    });
    const resolver = new RedisSessionResolver(redis, 60);
    const user = await resolver.resolve("abc");
    expect(user).toEqual({ id: "u-1", email: "a@b.co", roles: ["user", "admin"] });
    expect(redis.getex).toHaveBeenCalledWith("sess:abc", "EX", 60);
  });

  it("defaults roles to ['user'] for legacy sessions without the field", async () => {
    const redis = makeFakeRedis({
      "sess:legacy": JSON.stringify({
        userId: "u-1",
        email: "a@b.co",
        createdAt: "2024-01-01T00:00:00Z",
      }),
    });
    const resolver = new RedisSessionResolver(redis, 60);
    expect(await resolver.resolve("legacy")).toEqual({
      id: "u-1",
      email: "a@b.co",
      roles: ["user"],
    });
  });
});
