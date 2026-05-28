import { injectable } from "tsyringe";
import { Repository } from "typeorm";
import { AuditEntryInput, IAuditRepo } from "../../../domain/IRepos/IAuditRepo";
import { AuditLog } from "../../../domain/entities/audit-log.entity";
import { AppDataSource } from "../data-source";
import { getManager } from "../tx-context";

// The one repository for audit entries. Registered as "IAuditRepo".
@injectable()
export class AuditRepo implements IAuditRepo {
  private repo(): Repository<AuditLog> {
    return (getManager() ?? AppDataSource.manager).getRepository(AuditLog);
  }

  async record(entry: AuditEntryInput): Promise<void> {
    const repo = this.repo();
    await repo.save(
      repo.create({
        actorId: entry.actorId ?? null,
        action: entry.action,
        targetId: entry.targetId ?? null,
        details: entry.details ?? {},
        ip: entry.ip ?? null,
      }),
    );
  }
}
