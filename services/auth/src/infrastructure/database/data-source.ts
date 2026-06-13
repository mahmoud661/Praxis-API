import "dotenv/config";
import path from "path";
import { DataSource } from "typeorm";
import { User, AuditLog, OutboxEvent } from "../../domain/entities";

// The single TypeORM DataSource — used by the app AND by the typeorm CLI
// (npm run typeorm … -d points at this file; the CLI requires the file to
// export exactly ONE DataSource instance, so don't add a default export
// alongside the named one).
//
// Schema management:
//   dev   — `synchronize` keeps the schema in sync from the entities.
//   prod  — `synchronize` is OFF; run `npm run migration:run:prod` (compiled)
//           before boot. Migrations live in ./migrations and are wired in via
//           the glob below, which resolves to .ts under ts-node and .js in
//           the compiled dist.
//
// This module deliberately reads DATABASE_URL/NODE_ENV directly instead of
// using loadEnv(): the CLI must not demand Kafka/Redis/session secrets just
// to run a migration. (`dotenv/config` is idempotent — main.ts loads it too.)
const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) {
  throw new Error("DATABASE_URL is required (set it in the environment or .env)");
}

export const AppDataSource = new DataSource({
  type: "postgres",
  url: databaseUrl,
  synchronize: process.env.NODE_ENV !== "production",
  logging: ["error", "warn"],
  entities: [User, AuditLog, OutboxEvent],
  migrations: [path.join(__dirname, "migrations", "*.{ts,js}")],
});
