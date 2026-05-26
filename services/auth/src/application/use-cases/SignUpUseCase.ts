import { inject, injectable } from "tsyringe";
import { UseCase } from "../UseCase";
import { Result } from "../../domain/shared/Result";
import {
  ConflictException,
  DomainException,
} from "../../domain/shared/DomainException";
import { User } from "../../domain/entities/User";
import { Email } from "../../domain/value-objects/Email";
import { UserId } from "../../domain/value-objects/UserId";
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
import {
  EVENT_PUBLISHER,
  EventPublisher,
} from "../../domain/ports/EventPublisher";
import { LOGGER, Logger } from "../../domain/ports/Logger";
import {
  UNIT_OF_WORK,
  UnitOfWork,
} from "../../domain/ports/UnitOfWork";
import { AUDIT_LOG, AuditLog } from "../../domain/ports/AuditLog";
import { SignUpInput, AuthOutput } from "../dtos";

const AUTH_TOPIC = "auth.events.v1";

// Single responsibility: register a new user and start their session.
// Depends only on ports — no Postgres, no Redis, no Kafka imported here.
@injectable()
export class SignUpUseCase implements UseCase<SignUpInput, AuthOutput, DomainException> {
  constructor(
    @inject(USER_REPOSITORY) private readonly users: UserRepository,
    @inject(PASSWORD_HASHER) private readonly hasher: PasswordHasher,
    @inject(SESSION_STORE) private readonly sessions: SessionStore,
    @inject(EVENT_PUBLISHER) private readonly publisher: EventPublisher,
    @inject(UNIT_OF_WORK) private readonly uow: UnitOfWork,
    @inject(AUDIT_LOG) private readonly audit: AuditLog,
    @inject(LOGGER) private readonly logger: Logger,
  ) {}

  async execute(input: SignUpInput): Promise<Result<AuthOutput, DomainException>> {
    try {
      const email = Email.create(input.email);

      if (await this.users.existsByEmail(email)) {
        return Result.fail(new ConflictException("Email already in use"));
      }

      const passwordHash = await this.hasher.hash(input.password);
      const user = User.register({
        id: UserId.generate(),
        email,
        passwordHash,
      });

      // Persist user AND outbox events in the same DB transaction. The
      // EventPublisher is the OutboxEventPublisher adapter: `publish` writes
      // to the `outbox` table on the active transaction's client, so it
      // commits atomically with the user row. A background poller ships the
      // outbox rows to Kafka — no events are lost if the process crashes
      // after commit and before publish.
      await this.uow.run(async () => {
        await this.users.save(user);
        await this.publisher.publish(AUTH_TOPIC, user.pullEvents());
        await this.audit.record({
          actorId: user.id.value,
          action: "user.signup",
          targetId: user.id.value,
          details: { email: user.email.value, roles: [...user.roles] },
        });
      });

      const sessionId = await this.sessions.create({
        userId: user.id.value,
        email: user.email.value,
        roles: [...user.roles],
        createdAt: new Date().toISOString(),
      });

      this.logger.info("user registered", { userId: user.id.value });

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
