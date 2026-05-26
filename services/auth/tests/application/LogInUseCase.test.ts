import { beforeEach, describe, expect, it } from "vitest";
import "reflect-metadata";
import { LogInUseCase } from "../../src/application/use-cases/LogInUseCase";
import { SignUpUseCase } from "../../src/application/use-cases/SignUpUseCase";
import { UnauthenticatedException } from "../../src/domain/shared/DomainException";
import {
  CapturingAuditLog,
  CapturingEventPublisher,
  InMemorySessionStore,
  InMemoryUserRepository,
  NoOpUnitOfWork,
  SilentLogger,
  StubPasswordHasher,
} from "../helpers/fakes";

describe("LogInUseCase", () => {
  let users: InMemoryUserRepository;
  let hasher: StubPasswordHasher;
  let sessions: InMemorySessionStore;
  let audit: CapturingAuditLog;
  let logIn: LogInUseCase;

  beforeEach(async () => {
    users = new InMemoryUserRepository();
    hasher = new StubPasswordHasher();
    sessions = new InMemorySessionStore();
    audit = new CapturingAuditLog();
    // Seed an existing account via SignUp.
    const signUp = new SignUpUseCase(
      users,
      hasher,
      sessions,
      new CapturingEventPublisher(),
      new NoOpUnitOfWork(),
      audit,
      new SilentLogger(),
    );
    await signUp.execute({ email: "alice@example.com", password: "correcthorsebattery" });
    sessions.sessions.clear();
    audit.entries.length = 0;

    logIn = new LogInUseCase(users, hasher, sessions, audit);
  });

  it("returns a session on valid credentials", async () => {
    const result = await logIn.execute({
      email: "alice@example.com",
      password: "correcthorsebattery",
    });
    expect(result.isOk()).toBe(true);
    expect(result.getValue().email).toBe("alice@example.com");
    expect(sessions.sessions.size).toBe(1);
  });

  it("returns the same error for wrong password and unknown email (no enumeration)", async () => {
    const wrongPwd = await logIn.execute({
      email: "alice@example.com",
      password: "wrong-password-here",
    });
    const unknown = await logIn.execute({
      email: "ghost@nowhere.tld",
      password: "anyvalidlooking",
    });

    expect(wrongPwd.isFail()).toBe(true);
    expect(unknown.isFail()).toBe(true);
    expect(wrongPwd.getError()).toBeInstanceOf(UnauthenticatedException);
    expect(unknown.getError()).toBeInstanceOf(UnauthenticatedException);
    expect(wrongPwd.getError().message).toBe(unknown.getError().message);
    // Both attempts produced audit rows.
    expect(audit.entries.filter((e) => e.action === "user.login.failed")).toHaveLength(2);
  });

  it("normalizes email casing on login", async () => {
    const result = await logIn.execute({
      email: "ALICE@EXAMPLE.COM",
      password: "correcthorsebattery",
    });
    expect(result.isOk()).toBe(true);
  });
});
