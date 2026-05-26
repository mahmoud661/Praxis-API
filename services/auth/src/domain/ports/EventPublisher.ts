import { DomainEvent } from "../shared/DomainEvent";

export const EVENT_PUBLISHER = Symbol("EventPublisher");

// Topic is a string here because the domain doesn't care which transport.
// The Kafka adapter knows what to do with `auth.events.v1`; an in-memory
// adapter in tests can just collect events into an array.
export interface EventPublisher {
  publish(topic: string, events: ReadonlyArray<DomainEvent>): Promise<void>;
}
