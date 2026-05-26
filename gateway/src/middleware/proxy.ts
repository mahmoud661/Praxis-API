import { createProxyMiddleware, Options, fixRequestBody } from "http-proxy-middleware";
import type { Request, Response } from "express";
import type { ClientRequest } from "http";

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
