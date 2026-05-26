import { User } from "../entities/User";
import { Email } from "../value-objects/Email";
import { UserId } from "../value-objects/UserId";

// Token used by the DI container. Symbols guarantee uniqueness across modules.
export const USER_REPOSITORY = Symbol("UserRepository");

// Pure persistence contract — knows nothing about SQL, ORMs, or vendors.
// Two implementations can coexist (Postgres in prod, in-memory in tests).
export interface UserRepository {
  save(user: User): Promise<void>;
  findById(id: UserId): Promise<User | null>;
  findByEmail(email: Email): Promise<User | null>;
  existsByEmail(email: Email): Promise<boolean>;
}
