import { injectable } from "tsyringe";
import { Repository } from "typeorm";
import { IOutboxRepo, OutboxRow } from "../../../domain/IRepos/IOutboxRepo";
import { OutboxEvent } from "../../../domain/entities/outbox.entity";
import { AppDataSource } from "../data-source";
import { getManager } from "../tx-context";

// The one repository for outbox rows. Registered as "IOutboxRepo". Inserts
// run on the active transactional manager so events commit atomically with
// the business write.
@injectable()
export class OutboxRepo implements IOutboxRepo {
  private repo(): Repository<OutboxEvent> {
    return (getManager() ?? AppDataSource.manager).getRepository(OutboxEvent);
  }

  async add(rows: ReadonlyArray<OutboxRow>): Promise<void> {
    if (rows.length === 0) return;
    const repo = this.repo();
    await repo.save(
      rows.map((r) =>
        repo.create({
          aggregateId: r.aggregateId,
          topic: r.topic,
          eventName: r.eventName,
          payload: r.payload,
          headers: r.headers,
        }),
      ),
    );
  }
}
