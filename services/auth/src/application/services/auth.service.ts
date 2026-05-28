import { inject, injectable } from "tsyringe";
import { IAuthService } from "../../domain/IServices/IAuthService";
import { IUserRepo } from "../../domain/IRepos/IUserRepo";
import { IAuditRepo } from "../../domain/IRepos/IAuditRepo";
import { Result } from "../../domain/shared/Result";
import {
  ConflictException,
  DomainException,
  UnauthenticatedException,
} from "../../domain/shared/DomainException";
import { User } from "../../domain/entities/user.entity";
import { Email } from "../../domain/value-objects/Email";
import { PasswordHash } from "../../domain/value-objects/PasswordHash";
import { UserId } from "../../domain/value-objects/UserId";
import { SESSION_STORE, SessionStore } from "../../domain/ports/SessionStore";
import {
  PASSWORD_HASHER,
  PasswordHasher,
} from "../../domain/ports/PasswordHasher";
import {
  EVENT_PUBLISHER,
  EventPublisher,
} from "../../domain/ports/EventPublisher";
import { UNIT_OF_WORK, UnitOfWork } from "../../domain/ports/UnitOfWork";
import { LOGGER, Logger } from "../../domain/ports/Logger";
import {
  SignUpInput,
  LogInInput,
  AuthOutput,
  UserView,
  AuthContext,
} from "../../domain/dtos/auth.dto";

const AUTH_TOPIC = "auth.events.v1";

/**
 * AuthService — the application entry point for authentication.
 * Architecture: routes → controller → AuthService → repos/adapters.
 *
 * Declares what it needs by interface token; the DI container binds each one
 * by naming convention ("IUserRepo", "IAuditRepo") or explicit registration
 * (the Symbol ports). Imports no infrastructure.
 *
 * One repository per entity: users → User (Postgres), audit → AuditLog
 * (Postgres). Sessions live in Redis behind the SessionStore port.
 */
@injectable()
export class AuthService implements IAuthService {
  constructor(
    @inject("IUserRepo") private readonly users: IUserRepo,
    @inject("IAuditRepo") private readonly audit: IAuditRepo,
    @inject(SESSION_STORE) private readonly sessions: SessionStore,
    @inject(PASSWORD_HASHER) private readonly hasher: PasswordHasher,
    @inject(EVENT_PUBLISHER) private readonly publisher: EventPublisher,
    @inject(UNIT_OF_WORK) private readonly uow: UnitOfWork,
    @inject(LOGGER) private readonly logger: Logger,
  ) {}

  async signUp(
    input: SignUpInput,
    ctx?: AuthContext,
  ): Promise<Result<AuthOutput, DomainException>> {
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

      // Persist user, outbox events, and audit row in one transaction (the
      // UnitOfWork opens a TypeORM transaction and the repos join it). The
      // outbox row commits atomically with the user; the poller ships it to
      // Kafka — no events lost on crash.
      await this.uow.run(async () => {
        await this.users.save(user);
        await this.publisher.publish(AUTH_TOPIC, user.pullEvents());
        await this.audit.record({
          actorId: user.id,
          action: "user.signup",
          targetId: user.id,
          details: { email: user.email, roles: [...user.roles] },
          ip: ctx?.ip ?? null,
        });
      });

      const sessionId = await this.sessions.create({
        userId: user.id,
        email: user.email,
        roles: [...user.roles],
        createdAt: new Date().toISOString(),
      });

      this.logger.info("user registered", { userId: user.id });

      return Result.ok({ userId: user.id, email: user.email, sessionId });
    } catch (err) {
      if (err instanceof DomainException) return Result.fail(err);
      throw err;
    }
  }

  async logIn(
    input: LogInInput,
    ctx?: AuthContext,
  ): Promise<Result<AuthOutput, DomainException>> {
    try {
      const email = Email.create(input.email);
      const user = await this.users.findByEmail(email);

      // Same response for "no user" and "wrong password" — no user enumeration.
      const ok =
        user !== null &&
        (await this.hasher.verify(
          input.password,
          PasswordHash.fromHashedValue(user.passwordHash),
        ));
      if (!user || !ok) {
        // Visible in `docker logs` so ops can see brute-force attempts in real
        // time, on top of the audit row.
        this.logger.warn("login failed", {
          email: email.value,
          ip: ctx?.ip ?? null,
        });
        await this.audit
          .record({
            action: "user.login.failed",
            targetId: email.value,
            ip: ctx?.ip ?? null,
          })
          .catch(() => undefined);
        return Result.fail(new UnauthenticatedException("Invalid credentials"));
      }

      const sessionId = await this.sessions.create({
        userId: user.id,
        email: user.email,
        roles: [...user.roles],
        createdAt: new Date().toISOString(),
      });

      // Re-login: revoke the prior session bound to the cookie that came in.
      // Best-effort — a stale destroy shouldn't fail the login.
      if (ctx?.oldSessionId && ctx.oldSessionId !== sessionId) {
        await this.sessions
          .destroy(ctx.oldSessionId)
          .catch(() => undefined);
      }

      await this.audit
        .record({
          actorId: user.id,
          action: "user.login",
          targetId: user.id,
          ip: ctx?.ip ?? null,
        })
        .catch(() => undefined);

      return Result.ok({ userId: user.id, email: user.email, sessionId });
    } catch (err) {
      if (err instanceof DomainException) return Result.fail(err);
      throw err;
    }
  }

  async logOut(
    sessionId: string,
    ctx?: AuthContext,
  ): Promise<Result<void, DomainException>> {
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
          ip: ctx?.ip ?? null,
        })
        .catch(() => undefined);
    }
    return Result.ok(undefined);
  }

  async getCurrentUser(
    sessionId: string,
  ): Promise<Result<UserView, DomainException>> {
    if (!sessionId) {
      return Result.fail(new UnauthenticatedException("No session"));
    }
    const session = await this.sessions.read(sessionId);
    if (!session) {
      return Result.fail(new UnauthenticatedException("Session expired"));
    }
    const user = await this.users.findById(UserId.from(session.userId));
    if (!user) {
      return Result.fail(new UnauthenticatedException("User no longer exists"));
    }
    await this.sessions.refresh(sessionId);
    return Result.ok({ userId: user.id, email: user.email });
  }
}
