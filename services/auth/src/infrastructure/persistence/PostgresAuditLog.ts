import { AuditEntry, AuditLog } from "../../domain/ports/AuditLog";
import { PostgresConnection } from "./PostgresConnection";

export class PostgresAuditLog implements AuditLog {
  constructor(private readonly conn: PostgresConnection) {}

  async record(entry: AuditEntry): Promise<void> {
    await this.conn.exec().query(
      `INSERT INTO audit_log (actor_id, action, target_id, details, ip)
       VALUES ($1, $2, $3, $4::jsonb, $5::inet)`,
      [
        entry.actorId ?? null,
        entry.action,
        entry.targetId ?? null,
        JSON.stringify(entry.details ?? {}),
        entry.ip ?? null,
      ],
    );
  }
}
