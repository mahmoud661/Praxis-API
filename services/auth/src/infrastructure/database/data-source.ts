import { DataSource } from "typeorm";
import { User, AuditLog, OutboxEvent } from "../../domain/entities";
import { loadEnv } from "../config/Env";

// The single TypeORM DataSource. Schema is kept in sync from the entity
// definitions (`synchronize`) in dev — there is no migration folder. In
// production `synchronize` is OFF (the local docker stack runs with
// NODE_ENV=development, so it stays on there).
const env = loadEnv();

export const AppDataSource = new DataSource({
  type: "postgres",
  url: env.DATABASE_URL,
  synchronize: env.NODE_ENV !== "production",
  logging: ["error", "warn"],
  entities: [User, AuditLog, OutboxEvent],
});
