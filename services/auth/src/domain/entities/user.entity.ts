import { Column, Entity, Index, PrimaryColumn } from "typeorm";
import { BaseEntity } from "./base.entity";
import { Email } from "../value-objects/Email";
import { PasswordHash } from "../value-objects/PasswordHash";
import { UserId } from "../value-objects/UserId";
import { UserRegisteredEvent } from "../events/UserRegisteredEvent";

// User aggregate, persisted via TypeORM. Columns hold primitives; value
// objects (Email/PasswordHash/UserId) are used at the boundary to validate
// and normalize input, then their `.value` is stored.
@Entity("users")
export class User extends BaseEntity {
  @PrimaryColumn({ type: "uuid" })
  id!: string;

  @Index()
  @Column({ type: "varchar", unique: true, length: 320 })
  email!: string;

  @Column({ type: "varchar", name: "password_hash", length: 255 })
  passwordHash!: string;

  @Column({ type: "text", array: true, default: () => "'{user}'" })
  roles!: string[];

  // Brand-new user: default role 'user', emits UserRegistered. The id is
  // app-generated (UserId.generate) so the event can carry it before the row
  // is flushed.
  static register(args: {
    id: UserId;
    email: Email;
    passwordHash: PasswordHash;
    roles?: ReadonlyArray<string>;
  }): User {
    const user = new User();
    user.id = args.id.value;
    user.email = args.email.value;
    user.passwordHash = args.passwordHash.value;
    user.roles =
      args.roles && args.roles.length > 0 ? [...args.roles] : ["user"];

    const registeredAt = new Date().toISOString();
    user.addEvent(
      new UserRegisteredEvent({
        userId: user.id,
        email: user.email,
        registeredAt,
      }),
    );
    return user;
  }

  hasRole(role: string): boolean {
    return this.roles.includes(role);
  }

  // Guard rail: even if some future endpoint does `res.json(user)`, the bcrypt
  // hash never ships to the client. JSON.stringify (which Express uses)
  // honors toJSON.
  toJSON(): Record<string, unknown> {
    const { passwordHash: _passwordHash, ...safe } = this;
    return safe;
  }
}
