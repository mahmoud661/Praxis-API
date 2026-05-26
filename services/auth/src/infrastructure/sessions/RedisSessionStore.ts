import { randomBytes } from "crypto";
import Redis from "ioredis";
import {
  SessionData,
  SessionStore,
} from "../../domain/ports/SessionStore";

const KEY_PREFIX = "sess:";

export class RedisSessionStore implements SessionStore {
  constructor(
    private readonly redis: Redis,
    private readonly ttlSeconds: number,
  ) {}

  async create(data: SessionData): Promise<string> {
    const sid = randomBytes(32).toString("hex");
    await this.redis.set(
      this.key(sid),
      JSON.stringify(data),
      "EX",
      this.ttlSeconds,
    );
    return sid;
  }

  async read(sessionId: string): Promise<SessionData | null> {
    const raw = await this.redis.get(this.key(sessionId));
    return raw ? (JSON.parse(raw) as SessionData) : null;
  }

  async refresh(sessionId: string): Promise<void> {
    await this.redis.expire(this.key(sessionId), this.ttlSeconds);
  }

  async destroy(sessionId: string): Promise<void> {
    await this.redis.del(this.key(sessionId));
  }

  private key(sid: string): string {
    return `${KEY_PREFIX}${sid}`;
  }
}
