import { ValidationException } from "../shared/DomainException";

// Value object: equality is by value, immutable, self-validating.
export class Email {
  private static readonly REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  private constructor(public readonly value: string) {}

  static create(raw: string): Email {
    const normalized = raw.trim().toLowerCase();
    if (!Email.REGEX.test(normalized)) {
      throw new ValidationException(`Invalid email: ${raw}`);
    }
    return new Email(normalized);
  }

  equals(other: Email): boolean {
    return this.value === other.value;
  }
}
