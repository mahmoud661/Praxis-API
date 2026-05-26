import { describe, expect, it, vi } from "vitest";
import {
  makeAttachSession,
  requireSession,
} from "../src/middleware/session.middleware";
import {
  ResolvedUser,
  SessionResolver,
} from "../src/ports/SessionResolver";

class StubResolver implements SessionResolver {
  constructor(private readonly user: ResolvedUser | null) {}
  resolve = vi.fn(async () => this.user);
}

function fakeReq(overrides: Record<string, unknown> = {}): any {
  return { signedCookies: {}, ...overrides };
}
function fakeRes(): any {
  return {
    status: vi.fn().mockReturnThis(),
    json: vi.fn().mockReturnThis(),
  };
}

describe("attachSession", () => {
  it("calls next without user when no cookie present", async () => {
    const resolver = new StubResolver(null);
    const mw = makeAttachSession(resolver, "sid");
    const req = fakeReq();
    const next = vi.fn();
    await mw(req, fakeRes(), next);
    expect(req.user).toBeUndefined();
    expect(resolver.resolve).not.toHaveBeenCalled();
    expect(next).toHaveBeenCalledOnce();
  });

  it("attaches req.user when the resolver returns a user", async () => {
    const user: ResolvedUser = { id: "u-1", email: "a@b.co", roles: ["user"] };
    const resolver = new StubResolver(user);
    const mw = makeAttachSession(resolver, "sid");
    const req = fakeReq({ signedCookies: { sid: "abc123" } });
    const next = vi.fn();
    await mw(req, fakeRes(), next);
    expect(req.user).toEqual(user);
    expect(resolver.resolve).toHaveBeenCalledWith("abc123");
    expect(next).toHaveBeenCalledOnce();
  });

  it("calls next() with error when the resolver throws", async () => {
    const resolver: SessionResolver = {
      resolve: vi.fn(async () => {
        throw new Error("redis down");
      }),
    };
    const mw = makeAttachSession(resolver, "sid");
    const next = vi.fn();
    await mw(fakeReq({ signedCookies: { sid: "abc" } }), fakeRes(), next);
    expect(next).toHaveBeenCalledOnce();
    expect((next.mock.calls[0][0] as Error).message).toBe("redis down");
  });
});

describe("requireSession", () => {
  it("returns 401 when no user is attached", () => {
    const res = fakeRes();
    const next = vi.fn();
    requireSession(fakeReq(), res, next);
    expect(res.status).toHaveBeenCalledWith(401);
    expect(res.json).toHaveBeenCalledWith({ error: "UNAUTHENTICATED" });
    expect(next).not.toHaveBeenCalled();
  });

  it("forwards when a user is attached", () => {
    const res = fakeRes();
    const next = vi.fn();
    requireSession(
      fakeReq({ user: { id: "u-1", email: "a@b.co", roles: ["user"] } }),
      res,
      next,
    );
    expect(next).toHaveBeenCalledOnce();
    expect(res.status).not.toHaveBeenCalled();
  });
});

import { requireRole } from "../src/middleware/session.middleware";

describe("requireRole", () => {
  it("403 when caller lacks any required role", () => {
    const res = fakeRes();
    const next = vi.fn();
    requireRole("admin")(
      fakeReq({ user: { id: "u-1", email: "a@b.co", roles: ["user"] } }),
      res,
      next,
    );
    expect(res.status).toHaveBeenCalledWith(403);
    expect(next).not.toHaveBeenCalled();
  });

  it("passes when the caller has at least one allowed role", () => {
    const res = fakeRes();
    const next = vi.fn();
    requireRole("user", "admin")(
      fakeReq({ user: { id: "u-1", email: "a@b.co", roles: ["user"] } }),
      res,
      next,
    );
    expect(next).toHaveBeenCalledOnce();
    expect(res.status).not.toHaveBeenCalled();
  });
});
