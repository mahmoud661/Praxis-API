import { describe, expect, it } from "vitest";
import { Response } from "express";
import { respondWithResult } from "../../src/presentation/http/resultMapper";
import { Result } from "../../src/domain/shared/Result";
import {
  ConflictException,
  DomainException,
  InvariantViolation,
  NotFoundException,
  TooManyAttemptsException,
  UnauthenticatedException,
  ValidationException,
} from "../../src/domain/shared/DomainException";

// Minimal chainable stand-in for express.Response — just records what the
// mapper sets. No supertest needed to verify pure mapping logic.
function fakeRes(): {
  res: Response;
  sent: { status?: number; body?: unknown };
} {
  const sent: { status?: number; body?: unknown } = {};
  const res = {
    status(code: number) {
      sent.status = code;
      return res;
    },
    json(body: unknown) {
      sent.body = body;
      return res;
    },
  };
  return { res: res as unknown as Response, sent };
}

// Exercise the `return 500` fallback for exceptions without a mapping.
class UnmappedException extends DomainException {
  readonly code = "UNMAPPED";
}

describe("respondWithResult", () => {
  it("sends the value with the default 200 on Ok", () => {
    const { res, sent } = fakeRes();
    respondWithResult(res, Result.ok({ hello: "world" }));
    expect(sent.status).toBe(200);
    expect(sent.body).toEqual({ hello: "world" });
  });

  it("honors a custom success status", () => {
    const { res, sent } = fakeRes();
    respondWithResult(res, Result.ok({ id: "1" }), 201);
    expect(sent.status).toBe(201);
  });

  it.each([
    [new ValidationException("bad input"), 400],
    [new InvariantViolation("broken rule"), 400],
    [new UnauthenticatedException("who are you"), 401],
    [new NotFoundException("nope"), 404],
    [new ConflictException("taken"), 409],
    [new TooManyAttemptsException("slow down"), 429],
    [new UnmappedException("???"), 500],
  ] as const)("maps %s to HTTP %i", (err, status) => {
    const { res, sent } = fakeRes();
    respondWithResult(res, Result.fail<never, DomainException>(err));
    expect(sent.status).toBe(status);
    expect(sent.body).toEqual({ error: err.code, message: err.message });
  });
});
