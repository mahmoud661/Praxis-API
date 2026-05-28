import { z } from "zod";

// Boundary shapes between presentation and application. The zod schemas live
// here (one source of truth) and are reused by the controller to validate the
// HTTP body; a ZodError becomes a 400 via the error middleware.

export const SignUpSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8).max(128),
});

export const LogInSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

export type SignUpInput = z.infer<typeof SignUpSchema>;
export type LogInInput = z.infer<typeof LogInSchema>;

export interface AuthOutput {
  userId: string;
  email: string;
  sessionId: string;
}

export interface UserView {
  userId: string;
  email: string;
}

// Per-request context the controller threads into the service: things like
// client IP for audit, and the incoming session id (so re-login can revoke
// the old session). Not part of the HTTP body — sourced from req.
export interface AuthContext {
  ip?: string | null;
  oldSessionId?: string | null;
}
