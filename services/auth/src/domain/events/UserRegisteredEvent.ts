import { DomainEvent } from "../shared/DomainEvent";

export interface UserRegisteredPayload {
  userId: string;
  email: string;
  registeredAt: string;
}

export class UserRegisteredEvent extends DomainEvent<UserRegisteredPayload> {
  constructor(payload: UserRegisteredPayload) {
    super({
      eventName: "UserRegistered",
      aggregateId: payload.userId,
      payload,
    });
  }
}
