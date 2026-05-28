import { inject, injectable } from "tsyringe";
import { EventPublisher } from "../../domain/ports/EventPublisher";
import { DomainEvent } from "../../domain/shared/DomainEvent";
import { IOutboxRepo } from "../../domain/IRepos/IOutboxRepo";

// Adapter for EventPublisher that writes events to the outbox table via the
// OutboxRepo. Because the repo uses the active transactional manager, the
// event row commits atomically with the business row (transactional outbox).
// A separate poller (OutboxPoller) ships pending rows to Kafka.
@injectable()
export class OutboxEventPublisher implements EventPublisher {
  constructor(@inject("IOutboxRepo") private readonly outbox: IOutboxRepo) {}

  async publish(
    topic: string,
    events: ReadonlyArray<DomainEvent>,
  ): Promise<void> {
    if (events.length === 0) return;
    await this.outbox.add(
      events.map((e) => ({
        aggregateId: e.metadata.aggregateId,
        topic,
        eventName: e.metadata.eventName,
        payload: { metadata: e.metadata, payload: e.payload },
        headers: {
          "event-name": e.metadata.eventName,
          "event-id": e.metadata.eventId,
          "event-version": String(e.metadata.version),
        },
      })),
    );
  }
}
