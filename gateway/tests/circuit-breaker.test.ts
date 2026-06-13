import { describe, expect, it, vi } from "vitest";
import type { Request, Response } from "express";
import {
  CircuitBreaker,
  makeCircuitBreakerMiddleware,
} from "../src/middleware/circuit-breaker";

function fakeRes() {
  const handlers: Record<string, () => void> = {};
  const stub = {
    statusCode: 200,
    status: vi.fn(),
    json: vi.fn().mockReturnThis(),
    on: vi.fn(),
    emitFinish: () => handlers["finish"]?.(),
  };
  stub.status.mockImplementation((code: number) => {
    stub.statusCode = code;
    return stub;
  });
  stub.on.mockImplementation((event: string, cb: () => void) => {
    handlers[event] = cb;
    return stub;
  });
  return stub as unknown as typeof stub & Response;
}

describe("CircuitBreaker", () => {
  it("stays CLOSED below the failure threshold", () => {
    const breaker = new CircuitBreaker("svc", 3, 1000);
    breaker.recordFailure();
    breaker.recordFailure();
    expect(breaker.isOpen()).toBe(false);
  });

  it("trips CLOSED → OPEN once consecutive failures hit the threshold", () => {
    const breaker = new CircuitBreaker("svc", 3, 1000);
    breaker.recordFailure();
    breaker.recordFailure();
    breaker.recordFailure();
    expect(breaker.isOpen()).toBe(true);
  });

  it("rejects while OPEN and inside the reset window", () => {
    const breaker = new CircuitBreaker("svc", 1, 1000);
    breaker.recordFailure();
    expect(breaker.isOpen(Date.now() + 999)).toBe(true);
  });

  it("half-opens after the reset window elapses", () => {
    const breaker = new CircuitBreaker("svc", 1, 1000);
    breaker.recordFailure();
    // One probe request is let through once resetMs has passed.
    expect(breaker.isOpen(Date.now() + 1000)).toBe(false);
  });

  it("closes again when the half-open probe succeeds", () => {
    const breaker = new CircuitBreaker("svc", 1, 1000);
    breaker.recordFailure();
    expect(breaker.isOpen(Date.now() + 1000)).toBe(false); // → HALF_OPEN
    breaker.recordSuccess();
    expect(breaker.isOpen()).toBe(false);
    // Fully CLOSED: the failure counter restarted from zero.
    expect(breaker.isOpen(Date.now() + 5000)).toBe(false);
  });

  it("re-opens with a fresh timer when the half-open probe fails", () => {
    const breaker = new CircuitBreaker("svc", 1, 1000);
    breaker.recordFailure();
    expect(breaker.isOpen(Date.now() + 1000)).toBe(false); // → HALF_OPEN
    breaker.recordFailure();
    expect(breaker.isOpen()).toBe(true);
  });
});

describe("circuitBreakerMiddleware", () => {
  it("short-circuits with 503 when the breaker is OPEN", () => {
    const breaker = new CircuitBreaker("auth-service", 1, 60_000);
    breaker.recordFailure();
    const mw = makeCircuitBreakerMiddleware(breaker);
    const res = fakeRes();
    const next = vi.fn();
    mw({} as unknown as Request, res, next);
    expect(res.status).toHaveBeenCalledWith(503);
    expect(res.json).toHaveBeenCalledWith({
      error: "UPSTREAM_UNAVAILABLE",
      breaker: "auth-service",
    });
    expect(next).not.toHaveBeenCalled();
  });

  it("records a failure on 5xx responses and opens at the threshold", () => {
    const breaker = new CircuitBreaker("svc", 2, 60_000);
    const mw = makeCircuitBreakerMiddleware(breaker);
    for (let i = 0; i < 2; i++) {
      const res = fakeRes();
      const next = vi.fn();
      mw({} as unknown as Request, res, next);
      expect(next).toHaveBeenCalledOnce();
      res.statusCode = 502;
      res.emitFinish();
    }
    expect(breaker.isOpen()).toBe(true);
  });

  it("records a success on <500 responses, resetting the failure count", () => {
    const breaker = new CircuitBreaker("svc", 2, 60_000);
    breaker.recordFailure();
    const mw = makeCircuitBreakerMiddleware(breaker);
    const res = fakeRes();
    mw({} as unknown as Request, res, vi.fn());
    res.statusCode = 200;
    res.emitFinish();
    // One more failure alone shouldn't trip it — the count restarted.
    breaker.recordFailure();
    expect(breaker.isOpen()).toBe(false);
  });
});
