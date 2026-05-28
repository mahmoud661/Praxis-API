import rateLimit from "express-rate-limit";

// Per-IP brute-force protection on the credential endpoints. We trust the
// proxy chain (see app-server.ts), so `req.ip` is the real client.
//
// Tight on login (passwords) — a few attempts per minute is plenty for a
// human, and bcrypt is CPU-bound so we want a hard ceiling regardless.
// Looser on signup — legitimate batches (e.g. team onboarding) shouldn't trip.
//
// Response shape matches the rest of the API ({error, message}) so the
// frontend can render it the same way as other failures.

const limitMessage = (msg: string): { error: string; message: string } => ({
  error: "RATE_LIMITED",
  message: msg,
});

export const loginRateLimiter = rateLimit({
  windowMs: 60_000, // 1 minute
  limit: 5,
  standardHeaders: "draft-7",
  legacyHeaders: false,
  message: limitMessage("Too many login attempts; try again in a minute."),
});

export const signupRateLimiter = rateLimit({
  windowMs: 60 * 60_000, // 1 hour
  limit: 10,
  standardHeaders: "draft-7",
  legacyHeaders: false,
  message: limitMessage("Too many signups from this IP; try again later."),
});
