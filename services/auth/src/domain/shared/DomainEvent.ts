// Base class for events the domain emits. Pure — no framework, no
// infrastructure references. The eventId/occurredAt are stamped at
// construction so events are immutable from creation.
import { randomUUID } from "crypto";

export interface DomainEventMetadata {
  eventId: string;
  occurredAt: string;
  eventName: string;
  aggregateId: string;
  version: number;
}

export abstract class DomainEvent<TPayload = unknown> {
  readonly metadata: DomainEventMetadata;
  readonly payload: TPayload;

  protected constructor(args: {
    eventName: string;
    aggregateId: string;
    payload: TPayload;
    version?: number;
  }) {
    this.metadata = {
      eventId: randomUUID(),
      occurredAt: new Date().toISOString(),
      eventName: args.eventName,
      aggregateId: args.aggregateId,
      version: args.version ?? 1,
    };
    this.payload = args.payload;
  }
}
