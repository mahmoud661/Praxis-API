// OpenTelemetry is loaded via NODE_OPTIONS=--require ... before this file
// even runs (see docker-compose.yml). No import here on purpose.
import "reflect-metadata";
import "dotenv/config";
import { container } from "tsyringe";
import Redis from "ioredis";
import { Server } from "http";
import { registerDependencies } from "./presentation/di/container";
import { AppDataSource } from "./infrastructure/database/data-source";
import { AppServer } from "./presentation/http/app-server";
import { OutboxPoller } from "./infrastructure/messaging/OutboxPoller";
import { REDIS_TOKEN } from "./infrastructure/config/tokens";
import { LOGGER, Logger } from "./domain/ports/Logger";

const SHUTDOWN_DEADLINE_MS = 15_000;

async function main(): Promise<void> {
  // Wire the container (registers env, adapters, and auto-discovers repos +
  // services by convention).
  await registerDependencies();
  const logger = container.resolve<Logger>(LOGGER);

  // Connect + sync schema from entities (dev). No migration step.
  await AppDataSource.initialize();
  logger.info("database connected & schema synced");

  const poller = container.resolve(OutboxPoller);
  await poller.start();

  const server: Server = await container.resolve(AppServer).listen();
  const redis = container.resolve<Redis>(REDIS_TOKEN);

  let shuttingDown = false;
  const shutdown = async (signal: string): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info("shutting down", { signal });

    // Drain: stop accepting connections, wait for in-flight requests.
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

    await poller.stop().catch(() => undefined);
    await redis.quit().catch(() => undefined);
    await AppDataSource.destroy().catch(() => undefined);
    process.exit(0);
  };

  process.on("SIGTERM", () => void shutdown("SIGTERM"));
  process.on("SIGINT", () => void shutdown("SIGINT"));
}

main().catch((err) => {
  console.error("fatal", err);
  process.exit(1);
});
