import bcrypt from "bcrypt";
import { inject, injectable } from "tsyringe";
import { PasswordHasher } from "../../domain/ports/PasswordHasher";
import { PasswordHash } from "../../domain/value-objects/PasswordHash";
import { ValidationException } from "../../domain/shared/DomainException";
import { ENV_TOKEN, Env } from "../config/Env";

@injectable()
export class BcryptPasswordHasher implements PasswordHasher {
  private readonly rounds: number;

  constructor(@inject(ENV_TOKEN) env: Env) {
    this.rounds = env.BCRYPT_ROUNDS;
  }

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
