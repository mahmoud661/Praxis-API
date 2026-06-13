import { z } from "zod";

// Refine helper — zod's .url() only works on a whole string, so the
// CSV allow-list checks each entry itself.
const isUrl = (s: string): boolean => {
  try {
    new URL(s);
    return true;
  } catch {
    return false;
  }
};

const schema = z.object({
  NODE_ENV: z.string().default("development"),
  PORT: z.coerce.number().default(3000),
  PUBLIC_ORIGIN: z.string().url().default("http://localhost:3000"),
  // Comma-separated allow-list. CORS code splits + trims; every entry
  // must parse as a URL so a typo'd origin fails at boot, not at runtime.
  FRONTEND_ORIGINS: z
    .string()
    .default("http://localhost:5173")
    .refine(
      (csv) =>
        csv
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean)
          .every(isUrl),
      { message: "every FRONTEND_ORIGINS entry must be a valid URL" },
    ),
  REDIS_URL: z.string().url(),
  SESSION_COOKIE_NAME: z.string().default("sid"),
  SESSION_SECRET: z.string().min(32),
  SESSION_TTL_SECONDS: z.coerce.number().int().positive().default(86400),
  AUTH_SERVICE_URL: z.string().url(),
  AI_AGENTS_SERVICE_URL: z.string().url(),
  // Resilience knobs
  PROXY_CB_THRESHOLD: z.coerce.number().int().positive().default(5),
  PROXY_CB_RESET_MS: z.coerce.number().int().positive().default(15000),
  RATE_LIMIT_PER_MINUTE: z.coerce.number().int().positive().default(120),
});

export type GatewayConfig = z.infer<typeof schema> & {
  serviceName: string;
  frontendOrigins: string[];
};

export function loadConfig(env: NodeJS.ProcessEnv = process.env): GatewayConfig {
  const parsed = schema.safeParse(env);
  if (!parsed.success) {
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
