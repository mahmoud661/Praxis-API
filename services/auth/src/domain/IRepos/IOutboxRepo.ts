// DI token "IOutboxRepo" (impl class `OutboxRepo`).
//
// Writes outbox rows inside the active transaction. The OutboxPoller (infra)
// is what later ships pending rows to Kafka — that read/publish path is not
// part of this contract.
export interface OutboxRow {
  aggregateId: string;
  topic: string;
  eventName: string;
  payload: unknown;
  headers: Record<string, string>;
}

export interface IOutboxRepo {
  add(rows: ReadonlyArray<OutboxRow>): Promise<void>;
}
