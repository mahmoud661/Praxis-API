import { inject, injectable } from "tsyringe";
import { UseCase } from "../UseCase";
import { Result } from "../../domain/shared/Result";
import {
  DomainException,
  UnauthenticatedException,
} from "../../domain/shared/DomainException";
import {
  SESSION_STORE,
  SessionStore,
} from "../../domain/ports/SessionStore";
import {
  USER_REPOSITORY,
  UserRepository,
} from "../../domain/ports/UserRepository";
import { UserId } from "../../domain/value-objects/UserId";
import { UserView } from "../dtos";

@injectable()
export class GetCurrentUserUseCase implements UseCase<string, UserView, DomainException> {
  constructor(
    @inject(SESSION_STORE) private readonly sessions: SessionStore,
    @inject(USER_REPOSITORY) private readonly users: UserRepository,
  ) {}

  async execute(sessionId: string): Promise<Result<UserView, DomainException>> {
    if (!sessionId) {
      return Result.fail(new UnauthenticatedException("No session"));
    }
    const session = await this.sessions.read(sessionId);
    if (!session) {
      return Result.fail(new UnauthenticatedException("Session expired"));
    }
    const user = await this.users.findById(UserId.from(session.userId));
    if (!user) {
      return Result.fail(new UnauthenticatedException("User no longer exists"));
    }
    await this.sessions.refresh(sessionId);
    return Result.ok({ userId: user.id.value, email: user.email.value });
  }
}
