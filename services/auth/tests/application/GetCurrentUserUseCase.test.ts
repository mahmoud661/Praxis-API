import { beforeEach, describe, expect, it } from "vitest";
import "reflect-metadata";
import { GetCurrentUserUseCase } from "../../src/application/use-cases/GetCurrentUserUseCase";
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

describe("GetCurrentUserUseCase", () => {
  let users: InMemoryUserRepository;
  let sessions: InMemorySessionStore;
  let useCase: GetCurrentUserUseCase;
  let signupSessionId: string;

  beforeEach(async () => {
    users = new InMemoryUserRepository();
    sessions = new InMemorySessionStore();
    const signUp = new SignUpUseCase(
      users,
      new StubPasswordHasher(),
      sessions,
      new CapturingEventPublisher(),
      new NoOpUnitOfWork(),
      new CapturingAuditLog(),
      new SilentLogger(),
    );
    const r = await signUp.execute({ email: "a@b.co", password: "correcthorsebattery" });
    signupSessionId = r.getValue().sessionId;
    useCase = new GetCurrentUserUseCase(sessions, users);
  });

  it("returns the user when the session is valid", async () => {
    const r = await useCase.execute(signupSessionId);
    expect(r.isOk()).toBe(true);
    expect(r.getValue().email).toBe("a@b.co");
  });

  it("fails when no session id is provided", async () => {
    const r = await useCase.execute("");
    expect(r.isFail()).toBe(true);
    expect(r.getError()).toBeInstanceOf(UnauthenticatedException);
  });

  it("fails when the session does not exist (expired or revoked)", async () => {
    const r = await useCase.execute("sid-does-not-exist");
    expect(r.isFail()).toBe(true);
    expect(r.getError()).toBeInstanceOf(UnauthenticatedException);
  });

  it("fails when the user behind the session is gone", async () => {
    users.byId.clear();
    const r = await useCase.execute(signupSessionId);
    expect(r.isFail()).toBe(true);
    expect(r.getError()).toBeInstanceOf(UnauthenticatedException);
  });
});
