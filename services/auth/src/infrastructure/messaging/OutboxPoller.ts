import { CompressionTypes, Kafka, Producer, logLevel } from "kafkajs";
import { DataSource } from "typeorm";
import { inject, injectable } from "tsyringe";
import { Logger, LOGGER } from "../../domain/ports/Logger";
import { ENV_TOKEN, Env } from "../config/Env";
import { DATA_SOURCE_TOKEN } from "../config/tokens";

// Background worker: every `intervalMs`, claims a small batch of unpublished
// outbox rows, sends them to Kafka, then marks them published. Uses
// `FOR UPDATE SKIP LOCKED` so multiple replicas don't fight for rows.
//
// Failure handling: a failed publish increments `attempts` and records
// `last_error` — that bookkeeping COMMITS even though the publish failed
// (the claim transaction is reused; only the published_at stamp is skipped).
// Once attempts reaches OUTBOX_MAX_ATTEMPTS the row is parked: `dead_at` is
// stamped, the fetch query skips it, and an operator alert is logged. Parked
// rows are never deleted — `UPDATE outbox SET dead_at = NULL WHERE id = …`
// re-queues one for delivery.

interface PendingRow {
  id: string;
  aggregate_id: string;
  topic: string;
  event_name: string;
  payload: unknown; // serialized envelope
  headers: Record<string, string>;
  attempts: number;
}

// What to persist for one row whose publish just failed. Pure decision,
// extracted so the parking threshold is unit-testable without a database.
export interface PublishFailureUpdate {
  attempts: number; // new value (previous + 1)
  lastError: string;
  deadAt: Date | null; // non-null ⇒ the row is parked
}

export function decidePublishFailure(args: {
  attemptsSoFar: number;
  maxAttempts: number;
  error: string;
  now: Date;
}): PublishFailureUpdate {
  const attempts = args.attemptsSoFar + 1;
  return {
    attempts,
    lastError: args.error,
    deadAt: attempts >= args.maxAttempts ? args.now : null,
  };
}

// The subset of kafkajs' Producer the poller uses — also the seam unit tests
// stub (same pattern as the FakeRedis used by the login-tracker tests).
export type OutboxProducer = Pick<Producer, "connect" | "disconnect" | "send">;

@injectable()
export class OutboxPoller {
  private readonly kafka: Kafka;
  private producer: OutboxProducer | null = null;
  private timer: NodeJS.Timeout | null = null;
  private stopped = false;
  private readonly intervalMs: number;
  private readonly maxAttempts: number;
  private readonly batchSize = 100;

  constructor(
    @inject(LOGGER) private readonly logger: Logger,
    @inject(ENV_TOKEN) env: Env,
    @inject(DATA_SOURCE_TOKEN) private readonly dataSource: DataSource,
  ) {
    this.intervalMs = env.OUTBOX_POLL_INTERVAL_MS;
    this.maxAttempts = env.OUTBOX_MAX_ATTEMPTS;
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

  // One polling cycle. Public so tests (and a manual flush) can drive it
  // without the timer.
  async tick(): Promise<void> {
    const qr = this.dataSource.createQueryRunner();
    await qr.connect();
    try {
      await qr.startTransaction();
      // Parked rows (dead_at set) are excluded — they stay in the table for
      // forensics until an operator re-queues or removes them.
      const rows: PendingRow[] = await qr.query(
        `SELECT id, aggregate_id, topic, event_name, payload, headers, attempts
           FROM outbox
          WHERE published_at IS NULL
            AND dead_at IS NULL
          ORDER BY created_at
          LIMIT $1
          FOR UPDATE SKIP LOCKED`,
        [this.batchSize],
      );
      if (rows.length === 0) {
        await qr.commitTransaction();
        return;
      }

      // Group by topic to send in batches. Each topic batch succeeds or
      // fails on its own; one broken topic must not block the others.
      const byTopic = new Map<string, PendingRow[]>();
      for (const r of rows) {
        const list = byTopic.get(r.topic) ?? [];
        list.push(r);
        byTopic.set(r.topic, list);
      }

      const producer = this.producer!;
      const sentIds: string[] = [];
      const failed: { row: PendingRow; error: Error }[] = [];
      for (const [topic, batch] of byTopic.entries()) {
        try {
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
          sentIds.push(...batch.map((r) => r.id));
        } catch (err) {
          for (const row of batch) failed.push({ row, error: err as Error });
        }
      }

      if (sentIds.length > 0) {
        await qr.query(
          `UPDATE outbox SET published_at = now() WHERE id = ANY($1::uuid[])`,
          [sentIds],
        );
      }

      // Record failures on the SAME claim transaction (rows are still locked)
      // and commit — the bookkeeping must survive even though publishing
      // failed, otherwise attempts would never accumulate.
      const now = new Date();
      for (const { row, error } of failed) {
        const update = decidePublishFailure({
          attemptsSoFar: row.attempts,
          maxAttempts: this.maxAttempts,
          error: error.message,
          now,
        });
        await qr.query(
          `UPDATE outbox SET attempts = $2, last_error = $3, dead_at = $4 WHERE id = $1`,
          [row.id, update.attempts, update.lastError, update.deadAt],
        );
        if (update.deadAt) {
          this.logger.error("outbox event parked after max publish attempts", {
            id: row.id,
            eventName: row.event_name,
            attempts: update.attempts,
            lastError: update.lastError,
          });
        }
      }

      await qr.commitTransaction();
      if (sentIds.length > 0) {
        this.logger.debug("outbox flushed", { count: sentIds.length });
      }
      if (failed.length > 0) {
        this.logger.warn("outbox publish failed for batch", {
          count: failed.length,
          err: failed[0]!.error.message,
        });
      }
    } catch (err) {
      // Database-level failure (claim, stamp, or bookkeeping). Roll back —
      // the rows were never unlocked, so the next tick retries them whole.
      await qr.rollbackTransaction().catch(() => undefined);
      this.logger.error("outbox tick failed", { err: (err as Error).message });
    } finally {
      await qr.release();
    }
  }
}
