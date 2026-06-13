import { createProxyMiddleware, Options, fixRequestBody } from "http-proxy-middleware";
import type { RequestHandler, Request, Response } from "express";
import type { ClientRequest, IncomingMessage } from "http";
import type { Socket } from "net";
import type { Logger } from "../ports/Logger";

// Per-request mutation of the outbound proxy request. Extracted from the
// `proxyReq` event handler so it's unit-testable with mock ClientRequest /
// Request objects (the http-proxy-middleware event plumbing isn't).
export function prepareProxyRequest(
  proxyReq: ClientRequest,
  req: Request,
  opts: { requireAuth: boolean; forwardCookie?: boolean },
): void {
  // Defense in depth: identity headers are gateway-owned. Strip whatever
  // the client sent BEFORE conditionally injecting from the resolved
  // session — otherwise a forged `X-User-Id` would pass through untouched
  // on requireAuth:false routes.
  proxyReq.removeHeader("x-user-id");
  proxyReq.removeHeader("x-user-email");
  proxyReq.removeHeader("x-user-roles");
  if (!opts.forwardCookie) {
    proxyReq.removeHeader("cookie");
  }
  if (req.user) {
    proxyReq.setHeader("X-User-Id", req.user.id);
    proxyReq.setHeader("X-User-Email", req.user.email);
    proxyReq.setHeader("X-User-Roles", req.user.roles.join(","));
  } else if (opts.requireAuth) {
    proxyReq.destroy();
    return;
  }
  // express.json() has already consumed req.body. Replay it to the
  // upstream — otherwise POST/PUT/PATCH arrive with an empty body.
  fixRequestBody(proxyReq, req);
}

// Builds a proxy that forwards to a downstream internal service.
//
// `forwardCookie` controls whether the inbound `Cookie` header survives
// the hop:
//   - false (default) for downstream services — the gateway is the only
//     thing that talks to Redis; downstream services trust `X-User-Id`.
//   - true for the auth-service hop, because auth-service IS the cookie's
//     owner (it reads `/auth/me` and clears it on `/auth/logout`).
export function makeServiceProxy(opts: {
  target: string;
  pathRewrite?: Options["pathRewrite"];
  requireAuth: boolean;
  forwardCookie?: boolean;
  logger: Logger;
}) {
  return createProxyMiddleware({
    target: opts.target,
    changeOrigin: true,
    pathRewrite: opts.pathRewrite,
    xfwd: true,
    proxyTimeout: 30_000,
    timeout: 30_000,
    on: {
      proxyReq: (proxyReq: ClientRequest, req: Request) => {
        prepareProxyRequest(proxyReq, req, opts);
      },
      error: (err, _req, res) => {
        // Log the real failure server-side; clients get a generic body so
        // upstream internals (hostnames, connect errors) never leak.
        opts.logger.error("proxy upstream error", {
          target: opts.target,
          err: err.message,
        });
        if ("status" in (res as Response)) {
          (res as Response).status(502).json({ error: "BAD_GATEWAY" });
        }
      },
    },
  });
}

/**
 * WebSocket-aware proxy. Returns both the Express middleware (for path
 * matching) and the `upgrade` handler the HTTP server attaches to its
 * `upgrade` event. Session auth runs in `main.ts`'s upgrade handler BEFORE
 * this proxy sees the request — so by here we've already injected
 * `X-User-Id` on the upgrade request headers.
 */
export function makeWsProxy(opts: {
  target: string;
  pathRewrite?: Options["pathRewrite"];
}): RequestHandler & {
  upgrade?: (req: IncomingMessage, socket: Socket, head: Buffer) => void;
} {
  return createProxyMiddleware({
    target: opts.target,
    changeOrigin: true,
    ws: true,
    pathRewrite: opts.pathRewrite,
    proxyTimeout: 0, // WS streams can be long-lived; no proxy timeout
    timeout: 0,
  });
}
