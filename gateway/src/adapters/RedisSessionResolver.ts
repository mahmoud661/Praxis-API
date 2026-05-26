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
    const raw = await this.redis.get(`sess:${sessionId}`);
    if (!raw) return null;
    // Sliding session: every successful resolution extends the TTL.
    await this.redis.expire(`sess:${sessionId}`, this.ttlSeconds);
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
