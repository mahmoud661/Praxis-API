import { z } from "zod";

const schema = z.object({
  NODE_ENV: z.string().default("development"),
  PORT: z.coerce.number().default(3000),
  PUBLIC_ORIGIN: z.string().url().default("http://localhost:3000"),
  // Comma-separated allow-list. CORS code splits + trims.
  FRONTEND_ORIGINS: z.string().default("http://localhost:5173"),
  REDIS_URL: z.string().url(),
  SESSION_COOKIE_NAME: z.string().default("sid"),
  SESSION_SECRET: z.string().min(32),
  SESSION_TTL_SECONDS: z.coerce.number().default(86400),
  AUTH_SERVICE_URL: z.string().url(),
  AI_AGENTS_SERVICE_URL: z.string().url(),
  // Resilience knobs
  PROXY_RETRY_COUNT: z.coerce.number().default(2),
  PROXY_CB_THRESHOLD: z.coerce.number().default(5),
  PROXY_CB_RESET_MS: z.coerce.number().default(15000),
  RATE_LIMIT_PER_MINUTE: z.coerce.number().default(120),
});

export type GatewayConfig = z.infer<typeof schema> & {
  serviceName: string;
  frontendOrigins: string[];
};

export function loadConfig(): GatewayConfig {
  const parsed = schema.safeParse(process.env);
  if (!parsed.success) {
    // eslint-disable-next-line no-console
    console.error("Invalid gateway env:", parsed.error.flatten().fieldErrors);
    process.exit(1);
  }
  return {
    ...parsed.data,
    serviceName: "gateway",
    frontendOrigins: parsed.data.FRONTEND_ORIGINS.split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  };
}
