import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Request } from "express";
import type { ClientRequest } from "http";

// Stub fixRequestBody so we can assert prepareProxyRequest calls it (or
// doesn't, after destroying an unauthenticated request) without pulling
// real ClientRequest internals into the test.
vi.mock("http-proxy-middleware", async (importOriginal) => {
  const actual = await importOriginal<typeof import("http-proxy-middleware")>();
  return { ...actual, fixRequestBody: vi.fn() };
});

import { fixRequestBody } from "http-proxy-middleware";
import { prepareProxyRequest } from "../src/middleware/proxy";

function fakeProxyReq(): ClientRequest {
  return {
    removeHeader: vi.fn(),
    setHeader: vi.fn(),
    destroy: vi.fn(),
  } as unknown as ClientRequest;
}

function fakeReq(overrides: Record<string, unknown> = {}): Request {
  return { ...overrides } as unknown as Request;
}

const user = { id: "u-1", email: "a@b.co", roles: ["user", "admin"] };

describe("prepareProxyRequest", () => {
  beforeEach(() => {
    vi.mocked(fixRequestBody).mockClear();
  });

  it("always strips inbound X-User-* headers, even on requireAuth:false routes", () => {
    const proxyReq = fakeProxyReq();
    prepareProxyRequest(proxyReq, fakeReq(), { requireAuth: false, forwardCookie: true });
    expect(proxyReq.removeHeader).toHaveBeenCalledWith("x-user-id");
    expect(proxyReq.removeHeader).toHaveBeenCalledWith("x-user-email");
    expect(proxyReq.removeHeader).toHaveBeenCalledWith("x-user-roles");
    // No session → nothing injected; forged headers stay stripped.
    expect(proxyReq.setHeader).not.toHaveBeenCalled();
    expect(proxyReq.destroy).not.toHaveBeenCalled();
  });

  it("strips inbound identity headers BEFORE injecting from req.user", () => {
    const proxyReq = fakeProxyReq();
    prepareProxyRequest(proxyReq, fakeReq({ user }), { requireAuth: true });
    const stripOrder = proxyReq.removeHeader.mock.invocationCallOrder[0];
    const injectOrder = proxyReq.setHeader.mock.invocationCallOrder[0];
    expect(stripOrder).toBeLessThan(injectOrder);
  });

  it("injects X-User-* headers from the resolved session", () => {
    const proxyReq = fakeProxyReq();
    prepareProxyRequest(proxyReq, fakeReq({ user }), { requireAuth: true });
    expect(proxyReq.setHeader).toHaveBeenCalledWith("X-User-Id", "u-1");
    expect(proxyReq.setHeader).toHaveBeenCalledWith("X-User-Email", "a@b.co");
    expect(proxyReq.setHeader).toHaveBeenCalledWith("X-User-Roles", "user,admin");
    expect(proxyReq.destroy).not.toHaveBeenCalled();
    expect(fixRequestBody).toHaveBeenCalledOnce();
  });

  it("removes the cookie header when forwardCookie is false/omitted", () => {
    const proxyReq = fakeProxyReq();
    prepareProxyRequest(proxyReq, fakeReq({ user }), { requireAuth: true });
    expect(proxyReq.removeHeader).toHaveBeenCalledWith("cookie");
  });

  it("keeps the cookie header when forwardCookie is true (auth-service hop)", () => {
    const proxyReq = fakeProxyReq();
    prepareProxyRequest(proxyReq, fakeReq({ user }), {
      requireAuth: false,
      forwardCookie: true,
    });
    expect(proxyReq.removeHeader).not.toHaveBeenCalledWith("cookie");
  });

  it("destroys the upstream request when requireAuth and no user attached", () => {
    const proxyReq = fakeProxyReq();
    prepareProxyRequest(proxyReq, fakeReq(), { requireAuth: true });
    expect(proxyReq.destroy).toHaveBeenCalledOnce();
    expect(proxyReq.setHeader).not.toHaveBeenCalled();
    // Early return: no body replay onto a destroyed request.
    expect(fixRequestBody).not.toHaveBeenCalled();
  });
});
