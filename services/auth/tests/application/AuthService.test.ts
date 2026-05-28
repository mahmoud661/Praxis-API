import { beforeEach, describe, expect, it } from "vitest";
import "reflect-metadata";
import { AuthService } from "../../src/application/services/auth.service";
import {
  ConflictException,
  UnauthenticatedException,
} from "../../src/domain/shared/DomainException";
import {
  CapturingAuditRepo,
  CapturingEventPublisher,
  InMemorySessionStore,
  InMemoryUserRepo,
  NoOpUnitOfWork,
  SilentLogger,
  StubPasswordHasher,
} from "../helpers/fakes";

function build(): {
  users: InMemoryUserRepo;
  audit: CapturingAuditRepo;
  sessions: InMemorySessionStore;
  publisher: CapturingEventPublisher;
  hasher: StubPasswordHasher;
  service: AuthService;
} {
  const users = new InMemoryUserRepo();
  const audit = new CapturingAuditRepo();
  const sessions = new InMemorySessionStore();
  const publisher = new CapturingEventPublisher();
  const hasher = new StubPasswordHasher();
  // Constructor order matches AuthService: users, audit, sessions, hasher,
  // publisher, uow, logger. (The @inject tokens only matter when resolved
  // through the container; here we wire positionally.)
  const service = new AuthService(
    users,
    audit,
    sessions,
    hasher,
    publisher,
    new NoOpUnitOfWork(),
    new SilentLogger(),
  );
  return { users, audit, sessions, publisher, hasher, service };
}

describe("AuthService.signUp", () => {
  let ctx: ReturnType<typeof build>;
  beforeEach(() => {
    ctx = build();
  });

  it("creates a user, opens a session, and emits UserRegistered", async () => {
    const result = await ctx.service.signUp({
      email: "Alice@Example.com",
      password: "correcthorsebattery",
    });

    expect(result.isOk()).toBe(true);
    const out = result.getValue();
    expect(out.email).toBe("alice@example.com");
    expect(out.sessionId).toMatch(/^sid-/);

    expect(ctx.users.byId.size).toBe(1);
    expect(ctx.sessions.sessions.get(out.sessionId)?.userId).toBe(out.userId);
    expect(ctx.publisher.published).toHaveLength(1);
    expect(ctx.publisher.published[0].topic).toBe("auth.events.v1");
    expect(ctx.publisher.published[0].events[0].metadata.eventName).toBe(
      "UserRegistered",
    );
    expect(ctx.audit.entries).toHaveLength(1);
    expect(ctx.audit.entries[0].action).toBe("user.signup");
    expect(ctx.audit.entries[0].actorId).toBe(out.userId);
  });

  it("rejects duplicate email with ConflictException and does not publish", async () => {
    await ctx.service.signUp({ email: "a@b.co", password: "correcthorsebattery" });
    const second = await ctx.service.signUp({
      email: "a@b.co",
      password: "anothergoodpassword",
    });

    expect(second.isFail()).toBe(true);
    expect(second.getError()).toBeInstanceOf(ConflictException);
    expect(ctx.publisher.published).toHaveLength(1);
    expect(ctx.users.byId.size).toBe(1);
  });

  it("returns a domain error for an invalid email instead of throwing", async () => {
    const result = await ctx.service.signUp({
      email: "not-an-email",
      password: "correcthorsebattery",
    });
    expect(result.isFail()).toBe(true);
    expect(ctx.users.byId.size).toBe(0);
    expect(ctx.publisher.published).toHaveLength(0);
  });

  it("propagates infra publish failures (transactional outbox contract)", async () => {
    ctx.publisher.shouldFail = true;
    await expect(
      ctx.service.signUp({
        email: "carol@example.com",
        password: "correcthorsebattery",
      }),
    ).rejects.toThrow(/simulated publish failure/);
  });
});

describe("AuthService.logIn", () => {
  let ctx: ReturnType<typeof build>;
  beforeEach(async () => {
    ctx = build();
    await ctx.service.signUp({
      email: "alice@example.com",
      password: "correcthorsebattery",
    });
    ctx.sessions.sessions.clear();
    ctx.audit.entries.length = 0;
  });

  it("returns a session on valid credentials", async () => {
    const result = await ctx.service.logIn({
      email: "alice@example.com",
      password: "correcthorsebattery",
    });
    expect(result.isOk()).toBe(true);
    expect(result.getValue().email).toBe("alice@example.com");
    expect(ctx.sessions.sessions.size).toBe(1);
  });

  it("returns the same error for wrong password and unknown email (no enumeration)", async () => {
    const wrongPwd = await ctx.service.logIn({
      email: "alice@example.com",
      password: "wrong-password-here",
    });
    const unknown = await ctx.service.logIn({
      email: "ghost@nowhere.tld",
      password: "anyvalidlooking",
    });

    expect(wrongPwd.isFail()).toBe(true);
    expect(unknown.isFail()).toBe(true);
    expect(wrongPwd.getError()).toBeInstanceOf(UnauthenticatedException);
    expect(unknown.getError()).toBeInstanceOf(UnauthenticatedException);
    expect(wrongPwd.getError().message).toBe(unknown.getError().message);
    expect(
      ctx.audit.entries.filter((e) => e.action === "user.login.failed"),
    ).toHaveLength(2);
  });

  it("normalizes email casing on login", async () => {
    const result = await ctx.service.logIn({
      email: "ALICE@EXAMPLE.COM",
      password: "correcthorsebattery",
    });
    expect(result.isOk()).toBe(true);
  });
});

describe("AuthService.logOut", () => {
  it("destroys the session and records an audit row", async () => {
    const ctx = build();
    const sid = await ctx.sessions.create({
      userId: "11111111-2222-4333-8444-555555555555",
      email: "a@b.co",
      roles: ["user"],
      createdAt: new Date().toISOString(),
    });

    const out = await ctx.service.logOut(sid);

    expect(out.isOk()).toBe(true);
    expect(ctx.sessions.sessions.get(sid)).toBeUndefined();
    expect(ctx.audit.entries).toHaveLength(1);
    expect(ctx.audit.entries[0].action).toBe("user.logout");
  });

  it("returns ok without audit when given empty session id", async () => {
    const ctx = build();
    const out = await ctx.service.logOut("");
    expect(out.isOk()).toBe(true);
    expect(ctx.audit.entries).toHaveLength(0);
  });
});

describe("AuthService.getCurrentUser", () => {
  let ctx: ReturnType<typeof build>;
  let signupSessionId: string;
  beforeEach(async () => {
    ctx = build();
    const r = await ctx.service.signUp({
      email: "a@b.co",
      password: "correcthorsebattery",
    });
    signupSessionId = r.getValue().sessionId;
  });

  it("returns the user when the session is valid", async () => {
    const r = await ctx.service.getCurrentUser(signupSessionId);
    expect(r.isOk()).toBe(true);
    expect(r.getValue().email).toBe("a@b.co");
  });

  it("fails when no session id is provided", async () => {
    const r = await ctx.service.getCurrentUser("");
    expect(r.isFail()).toBe(true);
    expect(r.getError()).toBeInstanceOf(UnauthenticatedException);
  });

  it("fails when the session does not exist (expired or revoked)", async () => {
    const r = await ctx.service.getCurrentUser("sid-does-not-exist");
    expect(r.isFail()).toBe(true);
    expect(r.getError()).toBeInstanceOf(UnauthenticatedException);
  });

  it("fails when the user behind the session is gone", async () => {
    ctx.users.byId.clear();
    const r = await ctx.service.getCurrentUser(signupSessionId);
    expect(r.isFail()).toBe(true);
    expect(r.getError()).toBeInstanceOf(UnauthenticatedException);
  });
});
