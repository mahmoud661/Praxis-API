import { randomUUID } from "crypto";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class UserId {
  private constructor(public readonly value: string) {}

  static generate(): UserId {
    return new UserId(randomUUID());
  }

  static from(raw: string): UserId {
    if (!UUID_RE.test(raw)) throw new Error(`Invalid UserId: ${raw}`);
    return new UserId(raw);
  }

  equals(other: UserId): boolean {
    return this.value === other.value;
  }
  toString(): string {
    return this.value;
  }
}
