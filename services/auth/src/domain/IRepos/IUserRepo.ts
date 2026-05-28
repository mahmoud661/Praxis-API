import { User } from "../entities/user.entity";
import { Email } from "../value-objects/Email";
import { UserId } from "../value-objects/UserId";

// DI token: the convention in the container derives "IUserRepo" from the
// implementation class name `UserRepo`. Consumers inject `@inject("IUserRepo")`.
//
// Persistence contract for the User aggregate — one repository per entity.
// Takes value objects at the boundary, returns hydrated User entities.
export interface IUserRepo {
  save(user: User): Promise<void>;
  findById(id: UserId): Promise<User | null>;
  findByEmail(email: Email): Promise<User | null>;
  existsByEmail(email: Email): Promise<boolean>;
}
