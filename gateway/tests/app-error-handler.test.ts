import { describe, expect, it, vi } from "vitest";
import type { NextFunction, Request, Response } from "express";
import { makeErrorHandler } from "../src/app";
import type { Logger } from "../src/ports/Logger";

// supertest isn't a devDependency, so the 4-arg handler is exercised
// directly with mock req/res — same coverage, no HTTP round-trip.

function stubLogger(): Logger & { error: ReturnType<typeof vi.fn> } {
  return {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
  };
}

function fakeReq(): Request {
  return { method: "POST", path: "/v1/agents/run" } as unknown as Request;
}

function fakeRes(overrides: Record<string, unknown> = {}): {
  headersSent: boolean;
  status: ReturnType<typeof vi.fn>;
  json: ReturnType<typeof vi.fn>;
} {
  return {
    headersSent: false,
    status: vi.fn().mockReturnThis(),
    json: vi.fn().mockReturnThis(),
    ...overrides,
  };
}

describe("makeErrorHandler", () => {
  it("responds 500 with a generic body that never echoes err.message", () => {
    const logger = stubLogger();
    const handler = makeErrorHandler(logger);
    const res = fakeRes();
    const next = vi.fn();

    handler(new Error("ECONNREFUSED redis:6379 — secret internals"), fakeReq(), res as unknown as Response, next as NextFunction);

    expect(res.status).toHaveBeenCalledWith(500);
    expect(res.json).toHaveBeenCalledWith({ error: "INTERNAL" });
    expect(JSON.stringify(res.json.mock.calls[0][0])).not.toContain("ECONNREFUSED");
    expect(next).not.toHaveBeenCalled();
  });

  it("logs the real error server-side via the Logger port", () => {
    const logger = stubLogger();
    const handler = makeErrorHandler(logger);

    handler(new Error("redis down"), fakeReq(), fakeRes() as unknown as Response, vi.fn() as NextFunction);

    expect(logger.error).toHaveBeenCalledWith("unhandled error", {
      method: "POST",
      path: "/v1/agents/run",
      err: "redis down",
    });
  });

  it("stringifies non-Error throwables for the log", () => {
    const logger = stubLogger();
    makeErrorHandler(logger)("string boom", fakeReq(), fakeRes() as unknown as Response, vi.fn() as NextFunction);
    expect(logger.error).toHaveBeenCalledWith(
      "unhandled error",
      expect.objectContaining({ err: "string boom" }),
    );
  });

  it("delegates to next(err) when headers are already sent", () => {
    const logger = stubLogger();
    const handler = makeErrorHandler(logger);
    const res = fakeRes({ headersSent: true });
    const next = vi.fn();
    const err = new Error("late failure");

    handler(err, fakeReq(), res as unknown as Response, next as NextFunction);

    expect(next).toHaveBeenCalledWith(err);
    expect(res.status).not.toHaveBeenCalled();
    expect(res.json).not.toHaveBeenCalled();
  });
});
