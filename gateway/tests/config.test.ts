import { afterEach, describe, expect, it, vi } from "vitest";
import { loadConfig } from "../src/config";

// loadConfig exits the process on bad env (fail-fast at boot). For tests,
// turn the exit into a throw and silence the console.error report.
function spyOnExit() {
  const exit = vi.spyOn(process, "exit").mockImplementation((() => {
    throw new Error("process.exit");
  }) as never);
  const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
  return { exit, error };
}

const validEnv = {
  REDIS_URL: "redis://localhost:6379",
  SESSION_SECRET: "0123456789abcdef0123456789abcdef",
  AUTH_SERVICE_URL: "http://auth-service:3001",
  AI_AGENTS_SERVICE_URL: "http://ai-agents-service:8000",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loadConfig", () => {
  it("parses a valid env and applies defaults", () => {
    const config = loadConfig({ ...validEnv });
    expect(config.serviceName).toBe("gateway");
    expect(config.PORT).toBe(3000);
    expect(config.SESSION_TTL_SECONDS).toBe(86400);
    expect(config.PROXY_CB_THRESHOLD).toBe(5);
    expect(config.PROXY_CB_RESET_MS).toBe(15000);
    expect(config.RATE_LIMIT_PER_MINUTE).toBe(120);
    expect(config.frontendOrigins).toEqual(["http://localhost:5173"]);
  });

  it("splits and trims the FRONTEND_ORIGINS CSV", () => {
    const config = loadConfig({
      ...validEnv,
      FRONTEND_ORIGINS: "http://localhost:3000, https://app.example.com ,",
    });
    expect(config.frontendOrigins).toEqual([
      "http://localhost:3000",
      "https://app.example.com",
    ]);
  });

  it("rejects a FRONTEND_ORIGINS entry that is not a URL", () => {
    const { exit } = spyOnExit();
    expect(() =>
      loadConfig({ ...validEnv, FRONTEND_ORIGINS: "http://ok.example.com,not a url" }),
    ).toThrow("process.exit");
    expect(exit).toHaveBeenCalledWith(1);
  });

  it("rejects a non-positive SESSION_TTL_SECONDS", () => {
    const { exit } = spyOnExit();
    expect(() => loadConfig({ ...validEnv, SESSION_TTL_SECONDS: "0" })).toThrow(
      "process.exit",
    );
    expect(exit).toHaveBeenCalledWith(1);
  });

  it("rejects negative resilience knobs", () => {
    const { exit } = spyOnExit();
    expect(() =>
      loadConfig({ ...validEnv, PROXY_CB_THRESHOLD: "-1" }),
    ).toThrow("process.exit");
    expect(() =>
      loadConfig({ ...validEnv, RATE_LIMIT_PER_MINUTE: "0" }),
    ).toThrow("process.exit");
    expect(exit).toHaveBeenCalledTimes(2);
  });

  it("rejects a SESSION_SECRET shorter than 32 chars", () => {
    spyOnExit();
    expect(() =>
      loadConfig({ ...validEnv, SESSION_SECRET: "too-short" }),
    ).toThrow("process.exit");
  });
});
