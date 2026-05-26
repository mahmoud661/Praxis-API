import { NextFunction, Request, Response } from "express";
import { SessionResolver } from "../ports/SessionResolver";

declare module "express-serve-static-core" {
  interface Request {
    user?: { id: string; email: string; roles: string[] };
  }
}

// Middleware factory. Takes a resolver via constructor injection so the
// middleware itself doesn't import Redis — testable with a fake resolver.
export function makeAttachSession(
  resolver: SessionResolver,
  cookieName: string,
) {
  return async function attachSession(
    req: Request,
    _res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const sid = req.signedCookies?.[cookieName] as string | undefined;
      if (sid) {
        const user = await resolver.resolve(sid);
        if (user) req.user = user;
      }
      next();
    } catch (err) {
      next(err);
    }
  };
}

export function requireSession(
  req: Request,
  res: Response,
  next: NextFunction,
): void {
  if (!req.user) {
    res.status(401).json({ error: "UNAUTHENTICATED" });
    return;
  }
  next();
}

// RBAC gate: caller's session must have at least one of the given roles.
// Returns 403 otherwise. Use chained after `requireSession`.
export function requireRole(...allowed: string[]) {
  return function roleGuard(
    req: Request,
    res: Response,
    next: NextFunction,
  ): void {
    const roles = req.user?.roles ?? [];
    if (!allowed.some((r) => roles.includes(r))) {
      res.status(403).json({ error: "FORBIDDEN", required: allowed });
      return;
    }
    next();
  };
}
