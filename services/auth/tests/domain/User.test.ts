import { describe, expect, it } from "vitest";
import { User } from "../../src/domain/entities/user.entity";
import { Email } from "../../src/domain/value-objects/Email";
import { PasswordHash } from "../../src/domain/value-objects/PasswordHash";
import { UserId } from "../../src/domain/value-objects/UserId";
import { UserRegisteredEvent } from "../../src/domain/events/UserRegisteredEvent";

const okHash = "$2b$12$" + "a".repeat(53);

describe("User entity", () => {
  describe("register", () => {
    it("creates a user and emits exactly one UserRegistered event", () => {
      const user = User.register({
        id: UserId.generate(),
        email: Email.create("Alice@Example.com"),
        passwordHash: PasswordHash.fromHashedValue(okHash),
      });

      // Value objects normalize input; the entity stores primitives.
      expect(user.email).toBe("alice@example.com");
      expect(user.passwordHash).toBe(okHash);
      expect(user.roles).toEqual(["user"]); // default role

      const events = user.pullEvents();
      expect(events).toHaveLength(1);
      expect(events[0]).toBeInstanceOf(UserRegisteredEvent);
      const e = events[0] as UserRegisteredEvent;
      expect(e.payload.userId).toBe(user.id);
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

    it("honors explicit roles and hasRole()", () => {
      const user = User.register({
        id: UserId.generate(),
        email: Email.create("bob@example.com"),
        passwordHash: PasswordHash.fromHashedValue(okHash),
        roles: ["user", "admin"],
      });
      expect(user.roles).toEqual(["user", "admin"]);
      expect(user.hasRole("admin")).toBe(true);
      expect(user.hasRole("nope")).toBe(false);
    });
  });
});
