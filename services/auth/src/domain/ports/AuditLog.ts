export const AUDIT_LOG = Symbol("AuditLog");

export interface AuditEntry {
  actorId?: string | null;
  action: string; // e.g. "user.signup", "user.login", "user.logout.failed"
  targetId?: string | null; // resource the action operated on
  details?: Record<string, unknown>;
  ip?: string | null;
}

// Append-only sink for security-relevant events. Writes happen INSIDE the
// active DB transaction (via the active client) so the audit trail commits
// atomically with the business write — no "user created but no audit row".
export interface AuditLog {
  record(entry: AuditEntry): Promise<void>;
}
