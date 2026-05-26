import { DomainEvent } from "./DomainEvent";

// Base aggregate root. Records domain events that the application layer
// will dispatch after a successful transaction (write-then-publish).
export abstract class AggregateRoot {
  private _events: DomainEvent[] = [];

  protected addEvent(event: DomainEvent): void {
    this._events.push(event);
  }

  pullEvents(): DomainEvent[] {
    const out = this._events;
    this._events = [];
    return out;
  }
}
