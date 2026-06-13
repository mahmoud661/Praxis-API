import { Email } from "../value-objects/Email";

export const LOGIN_ATTEMPT_TRACKER = Symbol("LoginAttemptTracker");

// Per-account brute-force guard. The endpoint rate limiter throttles by IP;
// this port throttles by *account*, so a distributed attack rotating IPs
// against one email still gets locked out. The policy thresholds (how many
// failures, how long the window/lock lasts) live in the adapter — the
// application only asks "is it locked?" and reports failures/successes.
export interface LoginAttemptTracker {
  /** Record a failed login for this account. Returns the failure count in the current window. */
  recordFailure(email: Email): Promise<number>;
  /** True while the account is locked out. */
  isLocked(email: Email): Promise<boolean>;
  /** Clear the failure counter (called on successful login). */
  reset(email: Email): Promise<void>;
}
