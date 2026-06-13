import { z } from "zod";

const schema = z.object({
  NODE_ENV: z.string().default("development"),
  PORT: z.coerce.number().default(3001),
  DATABASE_URL: z.string().url(),
  REDIS_URL: z.string().url(),
  KAFKA_BROKERS: z.string().min(1),
  SESSION_COOKIE_NAME: z.string().default("sid"),
  SESSION_TTL_SECONDS: z.coerce.number().default(86400),
  SESSION_SECRET: z.string().min(32, "SESSION_SECRET must be 32+ chars"),
  BCRYPT_ROUNDS: z.coerce.number().default(12),
  // Account lockout: LOCKOUT_MAX_FAILURES failed logins within
  // LOCKOUT_WINDOW_SECONDS lock the account for the full window.
  LOCKOUT_MAX_FAILURES: z.coerce.number().default(5),
  LOCKOUT_WINDOW_SECONDS: z.coerce.number().default(900),
  OUTBOX_POLL_INTERVAL_MS: z.coerce.number().default(1000),
  // After this many failed Kafka publishes an outbox row is parked (dead_at
  // set, excluded from polling) instead of retrying forever.
  OUTBOX_MAX_ATTEMPTS: z.coerce.number().default(10),
});

export const ENV_TOKEN = Symbol("Env");
export type Env = z.infer<typeof schema> & {
  serviceName: string;
  kafkaBrokers: string[];
};

export function loadEnv(): Env {
  const parsed = schema.safeParse(process.env);
  if (!parsed.success) {
    console.error("Invalid env:", parsed.error.flatten().fieldErrors);
    process.exit(1);
  }
  return {
    ...parsed.data,
    serviceName: "auth-service",
    kafkaBrokers: parsed.data.KAFKA_BROKERS.split(","),
  };
}
