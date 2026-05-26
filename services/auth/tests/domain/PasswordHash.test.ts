import { describe, expect, it } from "vitest";
import { PasswordHash } from "../../src/domain/value-objects/PasswordHash";

describe("PasswordHash", () => {
  it("accepts a normal-looking hash", () => {
    const hash = PasswordHash.fromHashedValue("$2b$12$" + "a".repeat(53));
    expect(hash.value).toMatch(/^\$2b\$12\$/);
  });

  it.each([
    ["empty", ""],
    ["too short", "short"],
  ])("rejects %s hash", (_label, raw) => {
    expect(() => PasswordHash.fromHashedValue(raw)).toThrow(/invalid hash/i);
  });
});
