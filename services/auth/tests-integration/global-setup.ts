import { PostgreSqlContainer, type StartedPostgreSqlContainer } from "@testcontainers/postgresql";

// Owns the lifecycle of the Postgres container that ALL integration test
// files share. Runs ONCE per worker pool, before any test file is imported.
//
// Why globalSetup (and not a beforeAll inside each file): `data-source.ts`
// reads `process.env.DATABASE_URL` at IMPORT TIME via `loadEnv()`. If we
// started the container inside the test file, the env var wouldn't be set
// until after data-source had already been frozen pointing at whatever
// stale URL was in the shell. By setting env vars before any test-file
// import resolves, the singleton DataSource gets the right config the
// first time.
//
// Image pin: `postgres:16-alpine` — small, deterministic, matches what the
// production image runs against.

let container: StartedPostgreSqlContainer | null = null;

export async function setup(): Promise<void> {
  container = await new PostgreSqlContainer("postgres:16-alpine")
    .withDatabase("auth_test")
    .withUsername("test")
    .withPassword("test")
    .start();

  // The repos read these via loadEnv(). DATABASE_URL is the only one
  // they actually use; the others are required by the schema's zod
  // validator so loadEnv() doesn't exit(1). Dummies are fine — no test
  // touches Redis or Kafka.
  process.env.DATABASE_URL = container.getConnectionUri();
  process.env.NODE_ENV = "test";
  process.env.REDIS_URL ??= "redis://localhost:6379";
  process.env.KAFKA_BROKERS ??= "localhost:9092";
  process.env.SESSION_SECRET ??=
    "integration-test-session-secret-32chars!!";
}

export async function teardown(): Promise<void> {
  if (container) {
    await container.stop();
    container = null;
  }
}
