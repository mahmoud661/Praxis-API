import { injectable } from "tsyringe";
import { QueryFailedError, Repository } from "typeorm";
import { IUserRepo } from "../../../domain/IRepos/IUserRepo";
import { User } from "../../../domain/entities/user.entity";
import { Email } from "../../../domain/value-objects/Email";
import { UserId } from "../../../domain/value-objects/UserId";
import { ConflictException } from "../../../domain/shared/DomainException";
import { AppDataSource } from "../data-source";
import { getManager } from "../tx-context";

// Postgres error code for unique_violation. If two concurrent signups slip
// past the existsByEmail check (which runs outside the tx), the UNIQUE index
// is the real guarantor — we translate the driver error here so it surfaces
// as a domain ConflictException (→ 409) instead of a leaked 500.
const PG_UNIQUE_VIOLATION = "23505";

// The one repository for the User entity. Registered by the DI container as
// "IUserRepo" via naming convention. Uses the active transactional manager
// when inside a UnitOfWork, else the default DataSource.
@injectable()
export class UserRepo implements IUserRepo {
  private repo(): Repository<User> {
    return (getManager() ?? AppDataSource.manager).getRepository(User);
  }

  async save(user: User): Promise<void> {
    try {
      await this.repo().save(user);
    } catch (err) {
      if (isUniqueViolation(err)) {
        throw new ConflictException("Email already in use");
      }
      throw err;
    }
  }

  async findById(id: UserId): Promise<User | null> {
    return this.repo().findOneBy({ id: id.value });
  }

  async findByEmail(email: Email): Promise<User | null> {
    return this.repo().findOneBy({ email: email.value });
  }

  async existsByEmail(email: Email): Promise<boolean> {
    return this.repo().existsBy({ email: email.value });
  }
}

function isUniqueViolation(err: unknown): boolean {
  if (!(err instanceof QueryFailedError)) return false;
  // TypeORM exposes the pg driver error here; its `code` is the SQLSTATE.
  const driverErr = (err as QueryFailedError & { driverError?: { code?: string } })
    .driverError;
  return driverErr?.code === PG_UNIQUE_VIOLATION;
}
