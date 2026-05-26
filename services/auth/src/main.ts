// OpenTelemetry is loaded via NODE_OPTIONS=--require ... before this file
// even runs (see docker-compose.yml). No import here on purpose.
import "reflect-metadata";
import "dotenv/config";
import { container } from "tsyringe";
import { buildContainer } from "./composition-root";
import { runMigrations } from "./infrastructure/persistence/migrations";
import { ExpressAppFactory } from "./presentation/http/ExpressAppFactory";
import { LOGGER, Logger } from "./domain/ports/Logger";

const SHUTDOWN_DEADLINE_MS = 15_000;

async function main(): Promise<void> {
  const handles = buildContainer();
  const logger = container.resolve<Logger>(LOGGER);

  await runMigrations(handles.pgConn, logger);
  await handles.outboxPoller.start();

  const factory = container.resolve(ExpressAppFactory);
  const app = factory.build();

  const server = app.listen(handles.env.PORT, () => {
    logger.info("auth-service listening", { port: handles.env.PORT });
  });

  let shuttingDown = false;
  const shutdown = async (signal: string): Promise<void> => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info("shutting down", { signal });

    // Stop accepting new connections, then wait for in-flight requests to
    // finish (up to a deadline). This is the actual "drain" — server.close()
    // only does it correctly if we await its callback.
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

    await handles.outboxPoller.stop().catch(() => undefined);
    await handles.redis.quit().catch(() => undefined);
    await handles.pgConn.close().catch(() => undefined);
    process.exit(0);
  };

  process.on("SIGTERM", () => void shutdown("SIGTERM"));
  process.on("SIGINT", () => void shutdown("SIGINT"));
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error("fatal", err);
  process.exit(1);
});
