import { Response } from "express";

// Small helper so controllers don't repeat cookie options. The cookie is
// the *presentation* of a session; the application layer only knows
// "session id" — never "cookie".
export function setSessionCookie(args: {
  res: Response;
  name: string;
  value: string;
  ttlSeconds: number;
  secure: boolean;
}): void {
  args.res.cookie(args.name, args.value, {
    httpOnly: true,
    secure: args.secure,
    sameSite: "lax",
    path: "/",
    maxAge: args.ttlSeconds * 1000,
    signed: true,
  });
}

export function clearSessionCookie(res: Response, name: string): void {
  res.clearCookie(name, { path: "/" });
}
