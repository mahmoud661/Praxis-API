import { describe, expect, it } from "vitest";
import { Email } from "../../src/domain/value-objects/Email";
import { ValidationException } from "../../src/domain/shared/DomainException";

describe("Email", () => {
  it("normalizes by trimming and lowercasing", () => {
    const email = Email.create("  ALICE@Example.COM  ");
    expect(email.value).toBe("alice@example.com");
  });

  it("considers normalized equals equal", () => {
    expect(Email.create("alice@example.com").equals(Email.create("ALICE@example.com"))).toBe(true);
  });

  it.each([
    ["missing @", "alice.example.com"],
    ["missing local", "@example.com"],
    ["missing domain", "alice@"],
    ["whitespace inside", "ali ce@example.com"],
    ["empty", ""],
  ])("rejects %s", (_label, raw) => {
    expect(() => Email.create(raw)).toThrow(ValidationException);
  });
});

