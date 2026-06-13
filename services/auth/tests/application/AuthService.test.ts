import { beforeEach, describe, expect, it } from "vitest";
import "reflect-metadata";
import { AuthService } from "../../src/application/services/auth.service";
import {
  ConflictException,
  TooManyAttemptsException,
  UnauthenticatedException,
} from "../../src/domain/shared/DomainException";
import {
  CapturingAuditRepo,
  CapturingEventPublisher,
  CapturingLogger,
  FakeLoginAttemptTracker,
  InMemorySessionStore,
  InMemoryUserRepo,
  NoOpUnitOfWork,
  StubPasswordHasher,
} from "../helpers/fakes";

function build(): {
  users: InMemoryUserRepo;
  audit: CapturingAuditRepo;
  sessions: InMemorySessionStore;
  publisher: CapturingEventPublisher;
  hasher: StubPasswordHasher;
  attempts: FakeLoginAttemptTracker;
  logger: CapturingLogger;
  service: AuthService;
} {
  const users = new InMemoryUserRepo();
  const audit = new CapturingAuditRepo();
  const sessions = new InMemorySessionStore();
  const publisher = new CapturingEventPublisher();
  const hasher = new StubPasswordHasher();
  const attempts = new FakeLoginAttemptTracker();
  const logger = new CapturingLogger();
  // Constructor order matches AuthService: users, audit, sessions, hasher,
  // publisher, uow, logger, attempts. (The @inject tokens only matter when
  // resolved through the container; here we wire positionally.)
  const service = new AuthService(
    users,
    audit,
    sessions,
    hasher,
    publisher,
    new NoOpUnitOfWork(),
    logger,
    attempts,
  );
  return { users, audit, sessions, publisher, hasher, attempts, logger, service };
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

  it("rotates the session on re-login: destroys the old one, issues a new one", async () => {
    const first = await ctx.service.logIn({
      email: "alice@example.com",
      password: "correcthorsebattery",
    });
    const oldSid = first.getValue().sessionId;

    const second = await ctx.service.logIn(
      { email: "alice@example.com", password: "correcthorsebattery" },
      { oldSessionId: oldSid },
    );

    const newSid = second.getValue().sessionId;
    expect(newSid).not.toBe(oldSid);
    expect(ctx.sessions.sessions.get(oldSid)).toBeUndefined();
    expect(ctx.sessions.sessions.get(newSid)?.email).toBe("alice@example.com");
  });

  it("still succeeds when the audit write fails, but warns with the action name", async () => {
    ctx.audit.shouldFail = true;

    const result = await ctx.service.logIn({
      email: "alice@example.com",
      password: "correcthorsebattery",
    });

    expect(result.isOk()).toBe(true);
    const warns = ctx.logger.byLevel("warn");
    const auditWarn = warns.find((w) => w.msg === "audit write failed");
    expect(auditWarn).toBeDefined();
    expect(auditWarn?.ctx?.action).toBe("user.login");
  });

  it("does not fail a rejected login when the audit write fails either", async () => {
    ctx.audit.shouldFail = true;

    const result = await ctx.service.logIn({
      email: "alice@example.com",
      password: "wrong-password-here",
    });

    expect(result.isFail()).toBe(true);
    expect(result.getError()).toBeInstanceOf(UnauthenticatedException);
    const auditWarn = ctx.logger
      .byLevel("warn")
      .find((w) => w.msg === "audit write failed");
    expect(auditWarn?.ctx?.action).toBe("user.login.failed");
  });
});

describe("AuthService.logIn — account lockout", () => {
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

  async function failLogin(email = "alice@example.com") {
    return ctx.service.logIn({ email, password: "wrong-password-here" });
  }

  it("locks after 5 failures: 1-4 are 401-shaped, 5+ are 429-shaped", async () => {
    for (let i = 0; i < 4; i++) {
      const r = await failLogin();
      expect(r.getError()).toBeInstanceOf(UnauthenticatedException);
    }
    // 5th failure trips the lock — already answered as TooManyAttempts.
    const fifth = await failLogin();
    expect(fifth.getError()).toBeInstanceOf(TooManyAttemptsException);

    // Subsequent attempts short-circuit on the lock.
    const sixth = await failLogin();
    expect(sixth.getError()).toBeInstanceOf(TooManyAttemptsException);
  });

  it("lockout response carries the same generic message as bad credentials", async () => {
    const failed = await failLogin();
    for (let i = 0; i < 4; i++) await failLogin();
    const locked = await failLogin();

    expect(locked.getError()).toBeInstanceOf(TooManyAttemptsException);
    expect(locked.getError().message).toBe(failed.getError().message);
  });

  it("audits 'user.login.locked' exactly once, when the lock engages", async () => {
    for (let i = 0; i < 7; i++) await failLogin();

    const lockedEntries = ctx.audit.entries.filter(
      (e) => e.action === "user.login.locked",
    );
    expect(lockedEntries).toHaveLength(1);
    expect(lockedEntries[0].targetId).toBe("alice@example.com");
    // Failed attempts BEFORE the lock are audited; locked rejections are not.
    expect(
      ctx.audit.entries.filter((e) => e.action === "user.login.failed"),
    ).toHaveLength(5);
  });

  it("rejects a locked account even with the CORRECT password, without verifying it", async () => {
    for (let i = 0; i < 5; i++) await failLogin();
    ctx.hasher.verifyCalls = 0;

    const r = await ctx.service.logIn({
      email: "alice@example.com",
      password: "correcthorsebattery",
    });

    expect(r.getError()).toBeInstanceOf(TooManyAttemptsException);
    expect(ctx.hasher.verifyCalls).toBe(0); // lock checked BEFORE verification
    expect(ctx.sessions.sessions.size).toBe(0);
  });

  it("locks unknown emails identically — lockout does not enumerate accounts", async () => {
    for (let i = 0; i < 4; i++) {
      const r = await failLogin("ghost@nowhere.tld");
      expect(r.getError()).toBeInstanceOf(UnauthenticatedException);
    }
    const locked = await failLogin("ghost@nowhere.tld");
    expect(locked.getError()).toBeInstanceOf(TooManyAttemptsException);
  });

  it("successful login resets the failure counter", async () => {
    for (let i = 0; i < 3; i++) await failLogin();
    expect(ctx.attempts.failures.get("alice@example.com")).toBe(3);

    const ok = await ctx.service.logIn({
      email: "alice@example.com",
      password: "correcthorsebattery",
    });
    expect(ok.isOk()).toBe(true);
    expect(ctx.attempts.failures.has("alice@example.com")).toBe(false);

    // The window starts fresh: 4 more failures still answer 401, not 429.
    for (let i = 0; i < 4; i++) {
      const r = await failLogin();
      expect(r.getError()).toBeInstanceOf(UnauthenticatedException);
    }
  });

  it("counts failures per account, not globally", async () => {
    await ctx.service.signUp({
      email: "bob@example.com",
      password: "correcthorsebattery",
    });
    for (let i = 0; i < 5; i++) await failLogin("alice@example.com");

    const bob = await ctx.service.logIn({
      email: "bob@example.com",
      password: "correcthorsebattery",
    });
    expect(bob.isOk()).toBe(true);
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

  it("refreshes the session TTL on every valid read (sliding expiry)", async () => {
    await ctx.service.getCurrentUser(signupSessionId);
    await ctx.service.getCurrentUser(signupSessionId);
    expect(ctx.sessions.refreshed).toEqual([signupSessionId, signupSessionId]);
  });

  it("does not refresh when the session is missing", async () => {
    await ctx.service.getCurrentUser("sid-does-not-exist");
    expect(ctx.sessions.refreshed).toHaveLength(0);
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
