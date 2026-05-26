import { EventPublisher } from "../../domain/ports/EventPublisher";
import { DomainEvent } from "../../domain/shared/DomainEvent";
import { PostgresConnection } from "../persistence/PostgresConnection";

// Adapter for EventPublisher that writes to the `outbox` table inside
// whatever transaction is currently active on the PostgresConnection.
//
// This is the "T" in "transactional outbox": the event row commits atomically
// with the business row. A separate poller (OutboxPoller) reads pending rows
// later and ships them to Kafka. Service crash between commit and poll =
// no lost event; the row stays pending and gets shipped on the next tick.
export class OutboxEventPublisher implements EventPublisher {
  constructor(private readonly conn: PostgresConnection) {}

  async publish(topic: string, events: ReadonlyArray<DomainEvent>): Promise<void> {
    if (events.length === 0) return;
    for (const e of events) {
      await this.conn.exec().query(
        `INSERT INTO outbox (aggregate_id, topic, event_name, payload, headers)
         VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)`,
        [
          e.metadata.aggregateId,
          topic,
          e.metadata.eventName,
          JSON.stringify({ metadata: e.metadata, payload: e.payload }),
          JSON.stringify({
            "event-name": e.metadata.eventName,
            "event-id": e.metadata.eventId,
            "event-version": String(e.metadata.version),
          }),
        ],
      );
    }
  }
}
