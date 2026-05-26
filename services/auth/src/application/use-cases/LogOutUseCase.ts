import { inject, injectable } from "tsyringe";
import { UseCase } from "../UseCase";
import { Result } from "../../domain/shared/Result";
import { DomainException } from "../../domain/shared/DomainException";
import {
  SESSION_STORE,
  SessionStore,
} from "../../domain/ports/SessionStore";
import { AUDIT_LOG, AuditLog } from "../../domain/ports/AuditLog";

@injectable()
export class LogOutUseCase implements UseCase<string, void, DomainException> {
  constructor(
    @inject(SESSION_STORE) private readonly sessions: SessionStore,
    @inject(AUDIT_LOG) private readonly audit: AuditLog,
  ) {}

  async execute(sessionId: string): Promise<Result<void, DomainException>> {
    if (!sessionId) return Result.ok(undefined);
    // Audit BEFORE destruction so we can record who's logging out.
    const session = await this.sessions.read(sessionId).catch(() => null);
    await this.sessions.destroy(sessionId);
    if (session) {
      await this.audit
        .record({
          actorId: session.userId,
          action: "user.logout",
          targetId: session.userId,
        })
        .catch(() => undefined);
    }
    return Result.ok(undefined);
  }
}
