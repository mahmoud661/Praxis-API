import { describe, expect, it } from "vitest";
import { User } from "../../src/domain/entities/User";
import { Email } from "../../src/domain/value-objects/Email";
import { PasswordHash } from "../../src/domain/value-objects/PasswordHash";
import { UserId } from "../../src/domain/value-objects/UserId";
import { UserRegisteredEvent } from "../../src/domain/events/UserRegisteredEvent";

const okHash = "$2b$12$" + "a".repeat(53);

describe("User", () => {
  describe("register", () => {
    it("creates a user and emits exactly one UserRegistered event", () => {
      const user = User.register({
        id: UserId.generate(),
        email: Email.create("alice@example.com"),
        passwordHash: PasswordHash.fromHashedValue(okHash),
      });

      const events = user.pullEvents();
      expect(events).toHaveLength(1);
      expect(events[0]).toBeInstanceOf(UserRegisteredEvent);
      const e = events[0] as UserRegisteredEvent;
      expect(e.payload.userId).toBe(user.id.value);
      expect(e.payload.email).toBe("alice@example.com");
      expect(e.metadata.eventName).toBe("UserRegistered");
    });

    it("pullEvents drains — second call returns empty", () => {
      const user = User.register({
        id: UserId.generate(),
        email: Email.create("a@b.co"),
        passwordHash: PasswordHash.fromHashedValue(okHash),
      });
      expect(user.pullEvents()).toHaveLength(1);
      expect(user.pullEvents()).toHaveLength(0);
    });
  });

  describe("rehydrate", () => {
    it("rebuilds without emitting events (loading from DB must not republish)", () => {
      const user = User.rehydrate({
        id: UserId.from("11111111-2222-4333-8444-555555555555"),
        email: Email.create("bob@example.com"),
        passwordHash: PasswordHash.fromHashedValue(okHash),
        createdAt: new Date("2024-01-01T00:00:00Z"),
        roles: ["user", "admin"],
      });
      expect(user.pullEvents()).toHaveLength(0);
      expect(user.email.value).toBe("bob@example.com");
      expect(user.createdAt.toISOString()).toBe("2024-01-01T00:00:00.000Z");
      expect(user.roles).toEqual(["user", "admin"]);
      expect(user.hasRole("admin")).toBe(true);
      expect(user.hasRole("nope")).toBe(false);
    });
  });
});
