import { randomBytes } from "crypto";
import Redis from "ioredis";
import { inject, injectable } from "tsyringe";
import {
  SessionData,
  SessionStore,
} from "../../domain/ports/SessionStore";
import { ENV_TOKEN, Env } from "../config/Env";
import { REDIS_TOKEN } from "../config/tokens";

const KEY_PREFIX = "sess:";

// The one repository for Session data, backed by Redis.
@injectable()
export class RedisSessionStore implements SessionStore {
  private readonly ttlSeconds: number;

  constructor(
    @inject(REDIS_TOKEN) private readonly redis: Redis,
    @inject(ENV_TOKEN) env: Env,
  ) {
    this.ttlSeconds = env.SESSION_TTL_SECONDS;
  }

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
