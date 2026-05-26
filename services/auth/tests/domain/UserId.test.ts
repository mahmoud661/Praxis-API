import { describe, expect, it } from "vitest";
import { UserId } from "../../src/domain/value-objects/UserId";

describe("UserId", () => {
  it("generate() produces a valid UUID v4", () => {
    const id = UserId.generate();
    expect(id.value).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
  });

  it("from() accepts a valid UUID", () => {
    expect(UserId.from("11111111-2222-4333-8444-555555555555").value).toBe(
      "11111111-2222-4333-8444-555555555555",
    );
  });

  it("from() rejects non-UUID strings", () => {
    expect(() => UserId.from("not-a-uuid")).toThrow(/Invalid UserId/);
  });

  it("equals() compares by value", () => {
    const v = "11111111-2222-4333-8444-555555555555";
    expect(UserId.from(v).equals(UserId.from(v))).toBe(true);
    expect(UserId.generate().equals(UserId.generate())).toBe(false);
  });
});
