// OpenTelemetry is loaded via NODE_OPTIONS=--require ... before this file
// even runs (see docker-compose.yml). No import here on purpose.
import "dotenv/config";
import Redis from "ioredis";
import { loadConfig } from "./config";
import { PinoLogger } from "./adapters/PinoLogger";
import { RedisSessionResolver } from "./adapters/RedisSessionResolver";
import { buildApp } from "./app";

const SHUTDOWN_DEADLINE_MS = 15_000;

function main(): void {
  const config = loadConfig();
  const logger = new PinoLogger(
    config.serviceName,
    config.NODE_ENV === "production" ? "info" : "debug",
  );
  const redis = new Redis(config.REDIS_URL, { maxRetriesPerRequest: 3 });
  redis.on("error", (err) => logger.error("redis error", { err: err.message }));

  const resolver = new RedisSessionResolver(redis, config.SESSION_TTL_SECONDS);

  const app = buildApp({ config, resolver, redis, logger });
  const server = app.listen(config.PORT, () => {
    logger.info("gateway listening", { port: config.PORT });
  });

  let shuttingDown = false;
  const shutdown = async (signal: string): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info("shutting down", { signal });

    // Drain in-flight requests up to the deadline before exit.
    await new Promise<void>((resolve) => {
      const deadline = setTimeout(() => {
        logger.warn("shutdown drain timed out; forcing close");
        resolve();
      }, SHUTDOWN_DEADLINE_MS);
      server.close(() => {
        clearTimeout(deadline);
        resolve();
      });
    });

    await redis.quit().catch(() => undefined);
    process.exit(0);
  };

  process.on("SIGTERM", () => void shutdown("SIGTERM"));
  process.on("SIGINT", () => void shutdown("SIGINT"));
}

main();
