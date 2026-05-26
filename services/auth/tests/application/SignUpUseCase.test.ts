import { beforeEach, describe, expect, it } from "vitest";
import "reflect-metadata";
import { SignUpUseCase } from "../../src/application/use-cases/SignUpUseCase";
import { ConflictException } from "../../src/domain/shared/DomainException";
import {
  CapturingAuditLog,
  CapturingEventPublisher,
  InMemorySessionStore,
  InMemoryUserRepository,
  NoOpUnitOfWork,
  SilentLogger,
  StubPasswordHasher,
} from "../helpers/fakes";

function build(): {
  users: InMemoryUserRepository;
  hasher: StubPasswordHasher;
  sessions: InMemorySessionStore;
  publisher: CapturingEventPublisher;
  audit: CapturingAuditLog;
  useCase: SignUpUseCase;
} {
  const users = new InMemoryUserRepository();
  const hasher = new StubPasswordHasher();
  const sessions = new InMemorySessionStore();
  const publisher = new CapturingEventPublisher();
  const audit = new CapturingAuditLog();
  const useCase = new SignUpUseCase(
    users,
    hasher,
    sessions,
    publisher,
    new NoOpUnitOfWork(),
    audit,
    new SilentLogger(),
  );
  return { users, hasher, sessions, publisher, audit, useCase };
}

describe("SignUpUseCase", () => {
  let ctx: ReturnType<typeof build>;
  beforeEach(() => {
    ctx = build();
  });

  it("creates a user, opens a session, and emits UserRegistered", async () => {
    const result = await ctx.useCase.execute({
      email: "Alice@Example.com",
      password: "correcthorsebattery",
    });

    expect(result.isOk()).toBe(true);
    const out = result.getValue();
    expect(out.email).toBe("alice@example.com");
    expect(out.sessionId).toMatch(/^sid-/);

    // Stored
    expect(ctx.users.byId.size).toBe(1);
    // Session created
    expect(ctx.sessions.sessions.get(out.sessionId)?.userId).toBe(out.userId);
    // Event published exactly once on the right topic
    expect(ctx.publisher.published).toHaveLength(1);
    expect(ctx.publisher.published[0].topic).toBe("auth.events.v1");
    expect(ctx.publisher.published[0].events[0].metadata.eventName).toBe("UserRegistered");
    // Audit row written inside the same transaction
    expect(ctx.audit.entries).toHaveLength(1);
    expect(ctx.audit.entries[0].action).toBe("user.signup");
    expect(ctx.audit.entries[0].actorId).toBe(out.userId);
  });

  it("rejects duplicate email with ConflictException and does not publish", async () => {
    await ctx.useCase.execute({ email: "a@b.co", password: "correcthorsebattery" });
    const second = await ctx.useCase.execute({
      email: "a@b.co",
      password: "anothergoodpassword",
    });

    expect(second.isFail()).toBe(true);
    expect(second.getError()).toBeInstanceOf(ConflictException);
    expect(ctx.publisher.published).toHaveLength(1); // only the first signup
    expect(ctx.users.byId.size).toBe(1);
  });

  it("returns a domain error for an invalid email instead of throwing", async () => {
    const result = await ctx.useCase.execute({
      email: "not-an-email",
      password: "correcthorsebattery",
    });
    expect(result.isFail()).toBe(true);
    expect(ctx.users.byId.size).toBe(0);
    expect(ctx.publisher.published).toHaveLength(0);
  });

  it("propagates infra publish failures (transactional outbox contract)", async () => {
    // With the transactional-outbox publisher, the publish call happens
    // INSIDE the same DB transaction as the user save. If it throws, the
    // real PostgresUnitOfWork rolls the transaction back — no orphan user.
    //
    // The NoOpUnitOfWork fake can't model rollback (the in-memory repo
    // already has the row by then), so this test only asserts the error
    // bubbles out of the use case. Rollback-on-publish-failure is covered
    // by the integration tests that run against a real Postgres.
    ctx.publisher.shouldFail = true;
    await expect(
      ctx.useCase.execute({
        email: "carol@example.com",
        password: "correcthorsebattery",
      }),
    ).rejects.toThrow(/simulated publish failure/);
  });
});
