// Composition Root
// =================
// The only file in the codebase that imports concrete adapters together
// with their port tokens. Domain and application code stay free of any
// knowledge that Postgres / Redis / Kafka / bcrypt exist.

import "reflect-metadata";
import { container } from "tsyringe";
import Redis from "ioredis";

// Ports
import { USER_REPOSITORY } from "./domain/ports/UserRepository";
import { PASSWORD_HASHER } from "./domain/ports/PasswordHasher";
import { SESSION_STORE } from "./domain/ports/SessionStore";
import { EVENT_PUBLISHER } from "./domain/ports/EventPublisher";
import { LOGGER } from "./domain/ports/Logger";
import { UNIT_OF_WORK } from "./domain/ports/UnitOfWork";
import { AUDIT_LOG } from "./domain/ports/AuditLog";

// Adapters
import { PostgresConnection } from "./infrastructure/persistence/PostgresConnection";
import { PostgresUserRepository } from "./infrastructure/persistence/PostgresUserRepository";
import { PostgresUnitOfWork } from "./infrastructure/persistence/PostgresUnitOfWork";
import { PostgresAuditLog } from "./infrastructure/persistence/PostgresAuditLog";
import { BcryptPasswordHasher } from "./infrastructure/security/BcryptPasswordHasher";
import { RedisSessionStore } from "./infrastructure/sessions/RedisSessionStore";
import { OutboxEventPublisher } from "./infrastructure/messaging/OutboxEventPublisher";
import { OutboxPoller } from "./infrastructure/messaging/OutboxPoller";
import { PinoLoggerAdapter } from "./infrastructure/logging/PinoLoggerAdapter";

// Config
import { ENV_TOKEN, Env, loadEnv } from "./infrastructure/config/Env";

// Presentation tokens
import {
  POSTGRES_CONN_TOKEN,
  REDIS_TOKEN,
} from "./presentation/http/HealthController";

export interface AppHandles {
  env: Env;
  pgConn: PostgresConnection;
  redis: Redis;
  outboxPoller: OutboxPoller;
}

export function buildContainer(): AppHandles {
  const env = loadEnv();
  container.register<Env>(ENV_TOKEN, { useValue: env });

  // Logger first — other adapters depend on it.
  const logger = new PinoLoggerAdapter(
    env.serviceName,
    env.NODE_ENV === "production" ? "info" : "debug",
  );
  container.register(LOGGER, { useValue: logger });

  // Postgres connection + UoW + Repository (all share the same connection).
  const pgConn = new PostgresConnection(env.DATABASE_URL);
  container.register(POSTGRES_CONN_TOKEN, { useValue: pgConn });
  container.register(UNIT_OF_WORK, {
    useValue: new PostgresUnitOfWork(pgConn),
  });
  container.register(USER_REPOSITORY, {
    useValue: new PostgresUserRepository(pgConn),
  });
  container.register(AUDIT_LOG, {
    useValue: new PostgresAuditLog(pgConn),
  });

  // Password hashing.
  container.register(PASSWORD_HASHER, {
    useValue: new BcryptPasswordHasher(env.BCRYPT_ROUNDS),
  });

  // Redis client + session store.
  const redis = new Redis(env.REDIS_URL, { maxRetriesPerRequest: 3 });
  container.register(REDIS_TOKEN, { useValue: redis });
  container.register(SESSION_STORE, {
    useValue: new RedisSessionStore(redis, env.SESSION_TTL_SECONDS),
  });

  // Event publisher: writes to the Postgres `outbox` table inside the
  // current transaction. The OutboxPoller below ships rows to Kafka
  // asynchronously — so a crash between commit and ship doesn't lose events.
  container.register(EVENT_PUBLISHER, {
    useValue: new OutboxEventPublisher(pgConn),
  });

  const outboxPoller = new OutboxPoller(
    pgConn,
    env.kafkaBrokers,
    env.serviceName,
    logger,
    env.OUTBOX_POLL_INTERVAL_MS,
  );

  return { env, pgConn, redis, outboxPoller };
}
