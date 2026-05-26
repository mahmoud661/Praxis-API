import { User } from "../../domain/entities/User";
import { Email } from "../../domain/value-objects/Email";
import { PasswordHash } from "../../domain/value-objects/PasswordHash";
import { UserId } from "../../domain/value-objects/UserId";
import { UserRow } from "./UserRow";

// Pure translator: domain <-> row. Keeps both halves independent so
// persistence changes never leak into domain code and vice versa.
export const UserMapper = {
  toDomain(row: UserRow): User {
    return User.rehydrate({
      id: UserId.from(row.id),
      email: Email.create(row.email),
      passwordHash: PasswordHash.fromHashedValue(row.password_hash),
      createdAt: row.created_at,
      roles: row.roles ?? ["user"],
    });
  },

  toRow(user: User): UserRow {
    return {
      id: user.id.value,
      email: user.email.value,
      password_hash: user.passwordHash.value,
      created_at: user.createdAt,
      roles: [...user.roles],
    };
  },
};
