import { inject, injectable } from "tsyringe";
import { UseCase } from "../UseCase";
import { Result } from "../../domain/shared/Result";
import {
  DomainException,
  UnauthenticatedException,
} from "../../domain/shared/DomainException";
import { Email } from "../../domain/value-objects/Email";
import {
  USER_REPOSITORY,
  UserRepository,
} from "../../domain/ports/UserRepository";
import {
  PASSWORD_HASHER,
  PasswordHasher,
} from "../../domain/ports/PasswordHasher";
import {
  SESSION_STORE,
  SessionStore,
} from "../../domain/ports/SessionStore";
import { AUDIT_LOG, AuditLog } from "../../domain/ports/AuditLog";
import { LogInInput, AuthOutput } from "../dtos";

@injectable()
export class LogInUseCase implements UseCase<LogInInput, AuthOutput, DomainException> {
  constructor(
    @inject(USER_REPOSITORY) private readonly users: UserRepository,
    @inject(PASSWORD_HASHER) private readonly hasher: PasswordHasher,
    @inject(SESSION_STORE) private readonly sessions: SessionStore,
    @inject(AUDIT_LOG) private readonly audit: AuditLog,
  ) {}

  async execute(input: LogInInput): Promise<Result<AuthOutput, DomainException>> {
    try {
      const email = Email.create(input.email);
      const user = await this.users.findByEmail(email);

      // Same response for "no user" and "wrong password" — no user enumeration.
      const ok =
        user !== null && (await this.hasher.verify(input.password, user.passwordHash));
      if (!user || !ok) {
        // Audit failed attempt. Best-effort: never let an audit error block
        // the response. `targetId` is the email so failed-login analytics
        // can group by who was being attacked.
        await this.audit
          .record({
            action: "user.login.failed",
            targetId: email.value,
          })
          .catch(() => undefined);
        return Result.fail(new UnauthenticatedException("Invalid credentials"));
      }

      const sessionId = await this.sessions.create({
        userId: user.id.value,
        email: user.email.value,
        roles: [...user.roles],
        createdAt: new Date().toISOString(),
      });

      await this.audit
        .record({
          actorId: user.id.value,
          action: "user.login",
          targetId: user.id.value,
        })
        .catch(() => undefined);

      return Result.ok({
        userId: user.id.value,
        email: user.email.value,
        sessionId,
      });
    } catch (err) {
      if (err instanceof DomainException) return Result.fail(err);
      throw err;
    }
  }
}
