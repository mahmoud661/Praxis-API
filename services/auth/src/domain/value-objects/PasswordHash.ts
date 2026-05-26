// Hash-as-value-object. The domain never sees the plaintext; the hashing
// algorithm is a port (PasswordHasher), injected from infrastructure.
export class PasswordHash {
  private constructor(public readonly value: string) {}

  static fromHashedValue(hash: string): PasswordHash {
    if (!hash || hash.length < 20) {
      throw new Error("PasswordHash: invalid hash length");
    }
    return new PasswordHash(hash);
  }
}
