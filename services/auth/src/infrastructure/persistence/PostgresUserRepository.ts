import { UserRepository } from "../../domain/ports/UserRepository";
import { User } from "../../domain/entities/User";
import { Email } from "../../domain/value-objects/Email";
import { UserId } from "../../domain/value-objects/UserId";
import { PostgresConnection } from "./PostgresConnection";
import { UserMapper } from "./UserMapper";
import { UserRow } from "./UserRow";

const SELECT_COLS = "id, email, password_hash, created_at, roles";

export class PostgresUserRepository implements UserRepository {
  constructor(private readonly conn: PostgresConnection) {}

  async save(user: User): Promise<void> {
    const row = UserMapper.toRow(user);
    // Upsert by id — idempotent on retry.
    await this.conn.exec().query(
      `INSERT INTO users (id, email, password_hash, created_at, roles)
       VALUES ($1, $2, $3, $4, $5)
       ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email,
                                       password_hash = EXCLUDED.password_hash,
                                       roles = EXCLUDED.roles`,
      [row.id, row.email, row.password_hash, row.created_at, row.roles],
    );
  }

  async findById(id: UserId): Promise<User | null> {
    const { rows } = await this.conn
      .exec()
      .query<UserRow>(`SELECT ${SELECT_COLS} FROM users WHERE id = $1`, [id.value]);
    return rows[0] ? UserMapper.toDomain(rows[0]) : null;
  }

  async findByEmail(email: Email): Promise<User | null> {
    const { rows } = await this.conn
      .exec()
      .query<UserRow>(`SELECT ${SELECT_COLS} FROM users WHERE email = $1`, [
        email.value,
      ]);
    return rows[0] ? UserMapper.toDomain(rows[0]) : null;
  }

  async existsByEmail(email: Email): Promise<boolean> {
    const { rows } = await this.conn
      .exec()
      .query<{ exists: boolean }>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE email = $1) AS exists",
        [email.value],
      );
    return rows[0]?.exists ?? false;
  }
}
