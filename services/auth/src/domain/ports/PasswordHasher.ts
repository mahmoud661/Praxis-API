import { PasswordHash } from "../value-objects/PasswordHash";

export const PASSWORD_HASHER = Symbol("PasswordHasher");

// Algorithm is an infrastructure concern. The domain only knows "given
// a plaintext, produce a hash" and "given a plaintext + a hash, verify".
// Swap bcrypt for argon2 by adding a new adapter — no domain code changes.
export interface PasswordHasher {
  hash(plain: string): Promise<PasswordHash>;
  verify(plain: string, hash: PasswordHash): Promise<boolean>;
}
