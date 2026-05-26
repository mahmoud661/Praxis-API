import bcrypt from "bcrypt";
import { PasswordHasher } from "../../domain/ports/PasswordHasher";
import { PasswordHash } from "../../domain/value-objects/PasswordHash";
import { ValidationException } from "../../domain/shared/DomainException";

export class BcryptPasswordHasher implements PasswordHasher {
  constructor(private readonly rounds: number) {}

  async hash(plain: string): Promise<PasswordHash> {
    if (plain.length < 8 || plain.length > 128) {
      throw new ValidationException("Password must be 8–128 chars");
    }
    const h = await bcrypt.hash(plain, this.rounds);
    return PasswordHash.fromHashedValue(h);
  }

  async verify(plain: string, hash: PasswordHash): Promise<boolean> {
    return bcrypt.compare(plain, hash.value);
  }
}
