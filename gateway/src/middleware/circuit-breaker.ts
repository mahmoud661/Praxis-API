import type { Request, Response, NextFunction } from "express";

// Tiny per-target circuit breaker. State machine:
//   CLOSED  → counts consecutive failures (5xx or proxy network errors).
//             When failures >= threshold, trip to OPEN.
//   OPEN    → reject requests with 503 until `resetMs` has elapsed.
//             Then half-open: let exactly one request through.
//   HALF-OPEN→ on success, close. On failure, re-open with the timer reset.
//
// Wraps a downstream identifier (e.g. "auth-service") so each upstream has
// its own breaker — one bad downstream doesn't trip another.

type State = "CLOSED" | "OPEN" | "HALF_OPEN";

interface Breaker {
  state: State;
  failures: number;
  openedAt: number;
}

export class CircuitBreaker {
  private state: Breaker = { state: "CLOSED", failures: 0, openedAt: 0 };

  constructor(
    readonly name: string,
    private readonly threshold: number,
    private readonly resetMs: number,
  ) {}

  isOpen(now = Date.now()): boolean {
    if (this.state.state !== "OPEN") return false;
    if (now - this.state.openedAt >= this.resetMs) {
      this.state = { state: "HALF_OPEN", failures: 0, openedAt: 0 };
      return false;
    }
    return true;
  }

  recordSuccess(): void {
    this.state = { state: "CLOSED", failures: 0, openedAt: 0 };
  }

  recordFailure(): void {
    if (this.state.state === "HALF_OPEN") {
      this.state = { state: "OPEN", failures: this.threshold, openedAt: Date.now() };
      return;
    }
    const failures = this.state.failures + 1;
    this.state = {
      state: failures >= this.threshold ? "OPEN" : "CLOSED",
      failures,
      openedAt: failures >= this.threshold ? Date.now() : 0,
    };
  }
}

// Middleware factory: short-circuits to 503 when the breaker is OPEN.
// After the proxy responds, the proxy adapter calls back to record outcome.
export function makeCircuitBreakerMiddleware(breaker: CircuitBreaker) {
  return function circuitBreakerMiddleware(
    _req: Request,
    res: Response,
    next: NextFunction,
  ): void {
    if (breaker.isOpen()) {
      res.status(503).json({
        error: "UPSTREAM_UNAVAILABLE",
        breaker: breaker.name,
      });
      return;
    }
    // Record the outcome once the response is sent.
    res.on("finish", () => {
      if (res.statusCode >= 500 && res.statusCode !== 503) {
        breaker.recordFailure();
      } else if (res.statusCode < 500) {
        breaker.recordSuccess();
      }
    });
    next();
  };
}
