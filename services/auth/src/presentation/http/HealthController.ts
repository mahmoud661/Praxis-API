import { Request, Response, Router } from "express";
import { inject, injectable } from "tsyringe";
import { PostgresConnection } from "../../infrastructure/persistence/PostgresConnection";
import Redis from "ioredis";

export const REDIS_TOKEN = Symbol("Redis");
export const POSTGRES_CONN_TOKEN = Symbol("PostgresConnection");

@injectable()
export class HealthController {
  readonly router: Router;

  constructor(
    @inject(POSTGRES_CONN_TOKEN) private readonly conn: PostgresConnection,
    @inject(REDIS_TOKEN) private readonly redis: Redis,
  ) {
    this.router = Router();
    this.router.get("/healthz", this.liveness);
    this.router.get("/readyz", this.readiness);
  }

  private liveness = (_req: Request, res: Response): void => {
    res.json({ status: "ok" });
  };

  private readiness = async (_req: Request, res: Response): Promise<void> => {
    const checks: Record<string, string> = {};
    try {
      await this.conn.pool.query("SELECT 1");
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
  };
}
