import { createProxyMiddleware, Options, fixRequestBody } from "http-proxy-middleware";
import type { RequestHandler, Request, Response } from "express";
import type { ClientRequest, IncomingMessage } from "http";
import type { Socket } from "net";

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
      },
      error: (err, _req, res) => {
        if ("status" in (res as Response)) {
          (res as Response)
            .status(502)
            .json({ error: "BAD_GATEWAY", detail: err.message });
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
