import { CompressionTypes, Kafka, Producer, logLevel } from "kafkajs";
import { inject, injectable } from "tsyringe";
import { Logger, LOGGER } from "../../domain/ports/Logger";
import { ENV_TOKEN, Env } from "../config/Env";
import { AppDataSource } from "../database/data-source";

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

@injectable()
export class OutboxPoller {
  private readonly kafka: Kafka;
  private producer: Producer | null = null;
  private timer: NodeJS.Timeout | null = null;
  private stopped = false;
  private readonly intervalMs: number;
  private readonly batchSize = 100;

  constructor(
    @inject(LOGGER) private readonly logger: Logger,
    @inject(ENV_TOKEN) env: Env,
  ) {
    this.intervalMs = env.OUTBOX_POLL_INTERVAL_MS;
    this.kafka = new Kafka({
      brokers: env.kafkaBrokers,
      clientId: `${env.serviceName}-outbox`,
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
    const qr = AppDataSource.createQueryRunner();
    await qr.connect();
    try {
      await qr.startTransaction();
      const rows: PendingRow[] = await qr.query(
        `SELECT id, aggregate_id, topic, event_name, payload, headers
           FROM outbox
          WHERE published_at IS NULL
          ORDER BY created_at
          LIMIT $1
          FOR UPDATE SKIP LOCKED`,
        [this.batchSize],
      );
      if (rows.length === 0) {
        await qr.commitTransaction();
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
              typeof r.payload === "string"
                ? r.payload
                : JSON.stringify(r.payload),
            headers: Object.fromEntries(
              Object.entries(r.headers).map(([k, v]) => [k, String(v)]),
            ),
          })),
        });
      }

      await qr.query(
        `UPDATE outbox SET published_at = now() WHERE id = ANY($1::uuid[])`,
        [rows.map((r) => r.id)],
      );
      await qr.commitTransaction();
      this.logger.debug("outbox flushed", { count: rows.length });
    } catch (err) {
      await qr.rollbackTransaction().catch(() => undefined);
      this.logger.error("outbox tick failed", { err: (err as Error).message });
    } finally {
      await qr.release();
    }
  }
}
