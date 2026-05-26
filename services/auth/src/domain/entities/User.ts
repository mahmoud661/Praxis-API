import { AggregateRoot } from "../shared/AggregateRoot";
import { Email } from "../value-objects/Email";
import { PasswordHash } from "../value-objects/PasswordHash";
import { UserId } from "../value-objects/UserId";
import { UserRegisteredEvent } from "../events/UserRegisteredEvent";

interface UserProps {
  email: Email;
  passwordHash: PasswordHash;
  createdAt: Date;
  roles: ReadonlyArray<string>;
}

// Aggregate root. Holds invariants ("a user always has a valid email + a
// hashed password + at least one role"). Two named constructors:
//   - register: brand-new user, default role 'user', emits UserRegistered
//   - rehydrate: rebuild from persistence (no event emission)
export class User extends AggregateRoot {
  private constructor(
    public readonly id: UserId,
    private props: UserProps,
  ) {
    super();
  }

  static register(args: {
    id: UserId;
    email: Email;
    passwordHash: PasswordHash;
    roles?: ReadonlyArray<string>;
  }): User {
    const user = new User(args.id, {
      email: args.email,
      passwordHash: args.passwordHash,
      createdAt: new Date(),
      roles: args.roles && args.roles.length > 0 ? [...args.roles] : ["user"],
    });
    user.addEvent(
      new UserRegisteredEvent({
        userId: user.id.value,
        email: user.props.email.value,
        registeredAt: user.props.createdAt.toISOString(),
      }),
    );
    return user;
  }

  static rehydrate(args: {
    id: UserId;
    email: Email;
    passwordHash: PasswordHash;
    createdAt: Date;
    roles: ReadonlyArray<string>;
  }): User {
    return new User(args.id, {
      email: args.email,
      passwordHash: args.passwordHash,
      createdAt: args.createdAt,
      roles: [...args.roles],
    });
  }

  hasRole(role: string): boolean {
    return this.props.roles.includes(role);
  }

  get email(): Email {
    return this.props.email;
  }
  get passwordHash(): PasswordHash {
    return this.props.passwordHash;
  }
  get createdAt(): Date {
    return this.props.createdAt;
  }
  get roles(): ReadonlyArray<string> {
    return this.props.roles;
  }
}
