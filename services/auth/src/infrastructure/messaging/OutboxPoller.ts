import { CompressionTypes, Kafka, Producer, logLevel } from "kafkajs";
import { PostgresConnection } from "../persistence/PostgresConnection";
import { Logger } from "../../domain/ports/Logger";

// Background worker: every `intervalMs`, claims a small batch of unpublished
// outbox rows, sends them to Kafka, then marks them published. Uses
// `FOR UPDATE SKIP LOCKED` so multiple replicas don't fight for rows.

interface PendingRow {
  id: string;
  aggregate_id: string;
  topic: string;
  event_name: string;
  payload: unknown; // serialized envelope
  headers: Record<string, string>;
}

export class OutboxPoller {
  private readonly kafka: Kafka;
  private producer: Producer | null = null;
  private timer: NodeJS.Timeout | null = null;
  private stopped = false;

  constructor(
    private readonly conn: PostgresConnection,
    brokers: string[],
    clientId: string,
    private readonly logger: Logger,
    private readonly intervalMs: number,
    private readonly batchSize: number = 100,
  ) {
    this.kafka = new Kafka({
      brokers,
      clientId: `${clientId}-outbox`,
      logLevel: logLevel.WARN,
      retry: { initialRetryTime: 300, retries: 8 },
    });
  }

  async start(): Promise<void> {
    this.producer = this.kafka.producer({ idempotent: true });
    await this.producer.connect();
    this.logger.info("outbox poller started", { intervalMs: this.intervalMs });
    this.scheduleNext();
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    if (this.producer) await this.producer.disconnect();
  }

  private scheduleNext(): void {
    if (this.stopped) return;
    this.timer = setTimeout(() => {
      void this.tick().finally(() => this.scheduleNext());
    }, this.intervalMs);
  }

  private async tick(): Promise<void> {
    const client = await this.conn.pool.connect();
    try {
      await client.query("BEGIN");
      const { rows } = await client.query<PendingRow>(
        `SELECT id, aggregate_id, topic, event_name, payload, headers
           FROM outbox
          WHERE published_at IS NULL
          ORDER BY created_at
          LIMIT $1
          FOR UPDATE SKIP LOCKED`,
        [this.batchSize],
      );
      if (rows.length === 0) {
        await client.query("COMMIT");
        return;
      }

      // Group by topic to send in batches.
      const byTopic = new Map<string, PendingRow[]>();
      for (const r of rows) {
        const list = byTopic.get(r.topic) ?? [];
        list.push(r);
        byTopic.set(r.topic, list);
      }

      const producer = this.producer!;
      for (const [topic, batch] of byTopic.entries()) {
        await producer.send({
          topic,
          compression: CompressionTypes.GZIP,
          acks: -1,
          messages: batch.map((r) => ({
            key: r.aggregate_id,
            value:
              typeof r.payload === "string" ? r.payload : JSON.stringify(r.payload),
            headers: Object.fromEntries(
              Object.entries(r.headers).map(([k, v]) => [k, String(v)]),
            ),
          })),
        });
      }

      await client.query(
        `UPDATE outbox SET published_at = now() WHERE id = ANY($1::uuid[])`,
        [rows.map((r) => r.id)],
      );
      await client.query("COMMIT");
      this.logger.debug("outbox flushed", { count: rows.length });
    } catch (err) {
      await client.query("ROLLBACK").catch(() => undefined);
      this.logger.error("outbox tick failed", { err: (err as Error).message });
    } finally {
      client.release();
    }
  }
}
