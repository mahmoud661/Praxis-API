import { describe, expect, it } from "vitest";
import "reflect-metadata";
import { LogOutUseCase } from "../../src/application/use-cases/LogOutUseCase";
import {
  CapturingAuditLog,
  InMemorySessionStore,
} from "../helpers/fakes";

describe("LogOutUseCase", () => {
  it("destroys the session and records an audit row", async () => {
    const sessions = new InMemorySessionStore();
    const sid = await sessions.create({
      userId: "11111111-2222-4333-8444-555555555555",
      email: "a@b.co",
      roles: ["user"],
      createdAt: new Date().toISOString(),
    });
    const audit = new CapturingAuditLog();

    const out = await new LogOutUseCase(sessions, audit).execute(sid);

    expect(out.isOk()).toBe(true);
    expect(sessions.sessions.get(sid)).toBeUndefined();
    expect(audit.entries).toHaveLength(1);
    expect(audit.entries[0].action).toBe("user.logout");
  });

  it("returns ok without audit when given empty session id", async () => {
    const sessions = new InMemorySessionStore();
    const audit = new CapturingAuditLog();
    const out = await new LogOutUseCase(sessions, audit).execute("");
    expect(out.isOk()).toBe(true);
    expect(audit.entries).toHaveLength(0);
  });
});
