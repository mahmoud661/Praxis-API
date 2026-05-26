// Migration runner. Delegates to node-pg-migrate so we get versioned files,
// up/down semantics, and a real schema-migrations table — instead of the
// previous "CREATE TABLE IF NOT EXISTS" hack.
import { runner } from "node-pg-migrate";
import path from "path";
import { PostgresConnection } from "./PostgresConnection";
import { Logger } from "../../domain/ports/Logger";

export async function runMigrations(
  conn: PostgresConnection,
  logger?: Logger,
): Promise<void> {
  const result = await runner({
    databaseUrl: conn.url,
    dir: path.join(__dirname, "../../../migrations"),
    migrationsTable: "pgmigrations",
    direction: "up",
    count: Infinity,
    log: (msg: string) => logger?.info(msg),
  });
  logger?.info("migrations applied", { count: result.length });
}
