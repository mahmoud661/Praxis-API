// DI token "IAuditRepo" (impl class `AuditRepo`).
//
// Append-only audit sink. `record` writes inside whatever transaction is
// active, so the audit row commits atomically with the business write.
export interface AuditEntryInput {
  actorId?: string | null;
  action: string; // e.g. "user.signup", "user.login", "user.logout"
  targetId?: string | null;
  details?: Record<string, unknown>;
  ip?: string | null;
}

export interface IAuditRepo {
  record(entry: AuditEntryInput): Promise<void>;
}
