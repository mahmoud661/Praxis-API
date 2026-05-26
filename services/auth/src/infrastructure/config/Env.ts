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
  OUTBOX_POLL_INTERVAL_MS: z.coerce.number().default(1000),
});

export const ENV_TOKEN = Symbol("Env");
export type Env = z.infer<typeof schema> & {
  serviceName: string;
  kafkaBrokers: string[];
};

export function loadEnv(): Env {
  const parsed = schema.safeParse(process.env);
  if (!parsed.success) {
    // eslint-disable-next-line no-console
    console.error("Invalid env:", parsed.error.flatten().fieldErrors);
    process.exit(1);
  }
  return {
    ...parsed.data,
    serviceName: "auth-service",
    kafkaBrokers: parsed.data.KAFKA_BROKERS.split(","),
  };
}
