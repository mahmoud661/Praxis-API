import Redis from "ioredis";
import {
  ResolvedUser,
  SessionResolver,
} from "../ports/SessionResolver";

// Adapter. Same Redis key shape that auth-service writes.
// Both services agree on `sess:<id>` -> JSON({ userId, email, roles, createdAt }).
// If that shape ever changes, version the key prefix.
export class RedisSessionResolver implements SessionResolver {
  constructor(
    private readonly redis: Redis,
    private readonly ttlSeconds: number,
  ) {}

  async resolve(sessionId: string): Promise<ResolvedUser | null> {
    if (!sessionId) return null;
    // GETEX: atomic get + sliding TTL renewal in one round-trip (was two).
    const raw = await this.redis.getex(`sess:${sessionId}`, "EX", this.ttlSeconds);
    if (!raw) return null;
    const data = JSON.parse(raw) as {
      userId: string;
      email: string;
      roles?: string[];
    };
    return {
      id: data.userId,
      email: data.email,
      roles: data.roles ?? ["user"],
    };
  }
}
