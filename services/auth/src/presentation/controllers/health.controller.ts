import { Request, Response } from "express";
import { inject, injectable } from "tsyringe";
import Redis from "ioredis";
import { REDIS_TOKEN } from "../../infrastructure/config/tokens";
import { AppDataSource } from "../../infrastructure/database/data-source";

// Liveness + readiness probes. Liveness is a static "process is up"; readiness
// checks the real dependencies (Postgres via the DataSource, Redis).
@injectable()
export class HealthController {
  constructor(@inject(REDIS_TOKEN) private readonly redis: Redis) {}

  liveness(_req: Request, res: Response): void {
    res.json({ status: "ok" });
  }

  async readiness(_req: Request, res: Response): Promise<void> {
    const checks: Record<string, string> = {};
    try {
      await AppDataSource.query("SELECT 1");
      checks.db = "ok";
    } catch (err) {
      checks.db = (err as Error).message;
    }
    try {
      await this.redis.ping();
      checks.redis = "ok";
    } catch (err) {
      checks.redis = (err as Error).message;
    }
    const ready = Object.values(checks).every((v) => v === "ok");
    res.status(ready ? 200 : 503).json({ ready, checks });
  }
}
