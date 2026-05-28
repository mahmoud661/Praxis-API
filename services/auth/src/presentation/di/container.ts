// Composition Root (DDD bootstrap layer)
// =======================================
// In DDD/Onion, the wiring of which adapter implements which port is a
// *composition* concern — it lives at the outermost layer, not inside
// `infrastructure` (which only provides the adapters themselves). This file
// is the single place that imports concrete adapters together with their
// port tokens; everything inward of here stays unaware of Postgres / Redis /
// Kafka / bcrypt.
//
// Auto-wiring: this file does NOT import any repository or service directly.
// It globs `infrastructure/database/repos/` and `application/services/`,
// dynamic-imports each module, and registers every exported class as a
// singleton under a token derived from its name:
//
//   repos:    UserRepo    -> "IUserRepo"      ("I" + name without "Repo" + "Repo")
//   services: AuthService -> "IAuthService"   ("I" + class name)
//
// Consumers declare what they need by that token (e.g. @inject("IUserRepo")).
// Only genuine infrastructure adapters (logger, session store, hasher, event
// publisher, unit of work) and the entry-point classes are registered
// explicitly.

import "reflect-metadata";
import { container } from "tsyringe";
import { glob } from "glob";
import path from "path";
import Redis from "ioredis";

import { ENV_TOKEN, Env, loadEnv } from "../../infrastructure/config/Env";
import { REDIS_TOKEN } from "../../infrastructure/config/tokens";
import { LOGGER } from "../../domain/ports/Logger";
import { SESSION_STORE } from "../../domain/ports/SessionStore";
import { PASSWORD_HASHER } from "../../domain/ports/PasswordHasher";
import { EVENT_PUBLISHER } from "../../domain/ports/EventPublisher";
import { UNIT_OF_WORK } from "../../domain/ports/UnitOfWork";

import { PinoLoggerAdapter } from "../../infrastructure/logging/PinoLoggerAdapter";
import { RedisSessionStore } from "../../infrastructure/sessions/RedisSessionStore";
import { BcryptPasswordHasher } from "../../infrastructure/security/BcryptPasswordHasher";
import { OutboxEventPublisher } from "../../infrastructure/messaging/OutboxEventPublisher";
import { OutboxPoller } from "../../infrastructure/messaging/OutboxPoller";
import { TypeOrmUnitOfWork } from "../../infrastructure/database/TypeOrmUnitOfWork";
import { AppServer } from "../http/app-server";
import { AuthController } from "../controllers/auth.controller";
import { HealthController } from "../controllers/health.controller";

const repoToken = (className: string): string =>
  "I" + className.replace("Repo", "") + "Repo";
const serviceToken = (className: string): string => "I" + className;

// Glob a folder, dynamic-import every module, and register each exported
// class as a singleton under the token derived from its name.
async function registerByConvention(
  dir: string,
  tokenOf: (className: string) => string,
): Promise<void> {
  const files = await glob(path.join(dir, "*.@(ts|js)").replace(/\\/g, "/"));
  for (const file of files) {
    // Plain path (not a file:// URL): tsc compiles `import()` to `require()`
    // under CommonJS, and require resolves absolute paths but not file URLs.
    const mod = await import(file);
    for (const exportedName in mod) {
      const cls = mod[exportedName];
      if (typeof cls === "function") {
        container.registerSingleton(tokenOf(exportedName), cls);
      }
    }
  }
}

export async function registerDependencies(): Promise<Env> {
  const env = loadEnv();
  container.registerInstance<Env>(ENV_TOKEN, env);

  // Third-party client singleton.
  container.registerInstance<Redis>(
    REDIS_TOKEN,
    new Redis(env.REDIS_URL, { maxRetriesPerRequest: 3 }),
  );

  // Infrastructure adapters (not repos/services) — explicit singletons.
  container.registerSingleton(LOGGER, PinoLoggerAdapter);
  container.registerSingleton(SESSION_STORE, RedisSessionStore);
  container.registerSingleton(PASSWORD_HASHER, BcryptPasswordHasher);
  container.registerSingleton(EVENT_PUBLISHER, OutboxEventPublisher);
  container.registerSingleton(UNIT_OF_WORK, TypeOrmUnitOfWork);

  // Repositories + services — discovered and bound by naming convention.
  await registerByConvention(
    path.resolve(__dirname, "../../infrastructure/database/repos"),
    repoToken,
  );
  await registerByConvention(
    path.resolve(__dirname, "../../application/services"),
    serviceToken,
  );

  // Entry-point classes + background workers.
  container.registerSingleton(AuthController);
  container.registerSingleton(HealthController);
  container.registerSingleton(OutboxPoller);
  container.registerSingleton(AppServer);

  return env;
}
