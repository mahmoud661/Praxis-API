import { beforeEach, describe, expect, it } from "vitest";
import "reflect-metadata";
import { DataSource } from "typeorm";
import {
  OutboxPoller,
  OutboxProducer,
  decidePublishFailure,
} from "../../src/infrastructure/messaging/OutboxPoller";
import { Env } from "../../src/infrastructure/config/Env";
import { CapturingLogger } from "../helpers/fakes";

// Hand-rolled stand-ins for the two dependencies the poller talks to (same
// approach as the FakeRedis in RedisLoginAttemptTracker.test.ts): an
// in-memory outbox table behind a QueryRunner that understands the three
// statements the poller issues, and a producer whose failures are scripted
// per topic. The QueryRunner is transactional for real — changes are
// buffered from startTransaction and discarded on rollback — so the tests
// genuinely prove the failure bookkeeping COMMITS.

interface FakeRow {
  id: string;
  aggregate_id: string;
  topic: string;
  event_name: string;
  payload: unknown;
  headers: Record<string, string>;
  created_at: Date;
  published_at: Date | null;
  attempts: number;
  last_error: string | null;
  dead_at: Date | null;
}

class FakeOutboxDb {
  rows: FakeRow[] = [];
  begun = 0;
  committed = 0;
  rolledBack = 0;
  failNextSelect = false;
  private snapshot: FakeRow[] | null = null;

  // The poller calls dataSource.createQueryRunner() per tick.
  createQueryRunner() {
    return {
      connect: async () => undefined,
      release: async () => undefined,
      startTransaction: async () => {
        this.begun += 1;
        this.snapshot = this.rows.map((r) => ({ ...r }));
      },
      commitTransaction: async () => {
        this.committed += 1;
        this.snapshot = null;
      },
      rollbackTransaction: async () => {
        this.rolledBack += 1;
        if (this.snapshot) this.rows = this.snapshot;
        this.snapshot = null;
      },
      query: async (sql: string, params: unknown[] = []) =>
        this.query(sql, params),
    };
  }

  // Interprets only the statements the poller is expected to issue. The
  // SELECT applies exactly the predicates present in the SQL text — if the
  // implementation lost `dead_at IS NULL`, parked rows WOULD be returned
  // here and the exclusion test below would fail.
  private async query(sql: string, params: unknown[]): Promise<unknown> {
    if (sql.includes("SELECT") && sql.includes("FROM outbox")) {
      if (this.failNextSelect) {
        this.failNextSelect = false;
        throw new Error("simulated database outage");
      }
      let result = [...this.rows];
      if (sql.includes("published_at IS NULL")) {
        result = result.filter((r) => r.published_at === null);
      }
      if (sql.includes("dead_at IS NULL")) {
        result = result.filter((r) => r.dead_at === null);
      }
      result.sort((a, b) => a.created_at.getTime() - b.created_at.getTime());
      return result
        .slice(0, params[0] as number)
        .map(({ id, aggregate_id, topic, event_name, payload, headers, attempts }) => ({
          id,
          aggregate_id,
          topic,
          event_name,
          payload,
          headers,
          attempts,
        }));
    }
    if (sql.includes("SET published_at = now()")) {
      const ids = params[0] as string[];
      for (const row of this.rows) {
        if (ids.includes(row.id)) row.published_at = new Date();
      }
      return undefined;
    }
    if (sql.includes("SET attempts =")) {
      const [id, attempts, lastError, deadAt] = params as [
        string,
        number,
        string,
        Date | null,
      ];
      const row = this.rows.find((r) => r.id === id);
      if (!row) throw new Error(`FakeOutboxDb: no row ${id}`);
      row.attempts = attempts;
      row.last_error = lastError;
      row.dead_at = deadAt;
      return undefined;
    }
    throw new Error(`FakeOutboxDb: unrecognized SQL: ${sql}`);
  }
}

class FakeProducer {
  readonly sent: Array<{
    topic: string;
    messages: Array<{ key: string; value: string }>;
  }> = [];
  // Topics that should fail with the given error message.
  readonly failingTopics = new Map<string, string>();

  async connect(): Promise<void> {}
  async disconnect(): Promise<void> {}
  async send(record: {
    topic: string;
    messages: Array<{ key: string; value: string }>;
  }): Promise<unknown> {
    const failure = this.failingTopics.get(record.topic);
    if (failure) throw new Error(failure);
    this.sent.push({ topic: record.topic, messages: record.messages });
    return [];
  }
}

const MAX_ATTEMPTS = 3;

function makeEnv(): Env {
  return {
    OUTBOX_POLL_INTERVAL_MS: 1000,
    OUTBOX_MAX_ATTEMPTS: MAX_ATTEMPTS,
    serviceName: "auth-service",
    kafkaBrokers: ["localhost:9092"],
  } as Env; // the poller only reads these four fields
}

let seq = 0;
function makeRow(overrides: Partial<FakeRow> = {}): FakeRow {
  seq += 1;
  return {
    id: `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
    aggregate_id: `agg-${seq}`,
    topic: "auth.events.v1",
    event_name: "UserRegistered",
    payload: { userId: `u-${seq}` },
    headers: { "event-id": `e-${seq}` },
    created_at: new Date(2026, 0, 1, 0, 0, seq),
    published_at: null,
    attempts: 0,
    last_error: null,
    dead_at: null,
    ...overrides,
  };
}

describe("decidePublishFailure (pure parking decision)", () => {
  const now = new Date("2026-06-13T00:00:00Z");

  it("increments attempts and records the error, no parking below max", () => {
    const update = decidePublishFailure({
      attemptsSoFar: 0,
      maxAttempts: 10,
      error: "broker unreachable",
      now,
    });
    expect(update).toEqual({
      attempts: 1,
      lastError: "broker unreachable",
      deadAt: null,
    });
  });

  it("parks exactly when the incremented count reaches max", () => {
    expect(
      decidePublishFailure({ attemptsSoFar: 8, maxAttempts: 10, error: "x", now })
        .deadAt,
    ).toBeNull();
    expect(
      decidePublishFailure({ attemptsSoFar: 9, maxAttempts: 10, error: "x", now })
        .deadAt,
    ).toBe(now);
  });
});

describe("OutboxPoller.tick", () => {
  let db: FakeOutboxDb;
  let producer: FakeProducer;
  let logger: CapturingLogger;
  let poller: OutboxPoller;

  beforeEach(() => {
    db = new FakeOutboxDb();
    producer = new FakeProducer();
    logger = new CapturingLogger();
    poller = new OutboxPoller(
      logger,
      makeEnv(),
      db as unknown as DataSource,
    );
    // start() would connect a real Kafka producer; install the fake at the
    // same seam instead.
    (poller as unknown as { producer: OutboxProducer }).producer =
      producer as unknown as OutboxProducer;
  });

  it("success path: publishes pending rows and stamps published_at", async () => {
    db.rows.push(makeRow(), makeRow());

    await poller.tick();

    expect(producer.sent).toHaveLength(1); // one batch per topic
    expect(producer.sent[0]!.messages).toHaveLength(2);
    expect(db.rows.every((r) => r.published_at instanceof Date)).toBe(true);
    expect(db.rows.every((r) => r.attempts === 0)).toBe(true);
    expect(db.rows.every((r) => r.last_error === null)).toBe(true);
    expect(db.committed).toBe(1);
    expect(db.rolledBack).toBe(0);

    // Published rows are not picked up again.
    await poller.tick();
    expect(producer.sent).toHaveLength(1);
  });

  it("failed publish increments attempts and records the error — and commits it", async () => {
    db.rows.push(makeRow());
    producer.failingTopics.set("auth.events.v1", "broker unreachable");

    await poller.tick();

    const row = db.rows[0]!;
    expect(row.attempts).toBe(1);
    expect(row.last_error).toBe("broker unreachable");
    expect(row.dead_at).toBeNull();
    expect(row.published_at).toBeNull(); // still pending — will be retried
    // The bookkeeping survived: committed, NOT rolled back, despite the
    // publish failure.
    expect(db.committed).toBe(1);
    expect(db.rolledBack).toBe(0);
    expect(logger.byLevel("error")).toHaveLength(0); // not parked yet
  });

  it("reaching OUTBOX_MAX_ATTEMPTS parks the row and alerts the operator", async () => {
    const row = makeRow({ attempts: MAX_ATTEMPTS - 1 });
    db.rows.push(row);
    producer.failingTopics.set("auth.events.v1", "broker unreachable");

    await poller.tick();

    expect(db.rows[0]!.attempts).toBe(MAX_ATTEMPTS);
    expect(db.rows[0]!.dead_at).toBeInstanceOf(Date);
    expect(db.rows[0]!.published_at).toBeNull(); // parked, never deleted

    const errors = logger.byLevel("error");
    expect(errors).toHaveLength(1);
    expect(errors[0]!.msg).toContain("parked");
    expect(errors[0]!.ctx).toMatchObject({
      id: row.id,
      eventName: "UserRegistered",
      attempts: MAX_ATTEMPTS,
    });
  });

  it("parked rows are excluded from the fetch", async () => {
    db.rows.push(makeRow({ dead_at: new Date(), attempts: MAX_ATTEMPTS }));

    await poller.tick();

    expect(producer.sent).toHaveLength(0);
    expect(db.rows[0]!.attempts).toBe(MAX_ATTEMPTS); // untouched
    expect(db.rows[0]!.published_at).toBeNull();
  });

  it("an operator can re-queue a parked row by clearing dead_at", async () => {
    db.rows.push(makeRow({ dead_at: new Date(), attempts: MAX_ATTEMPTS }));
    db.rows[0]!.dead_at = null; // UPDATE outbox SET dead_at = NULL …

    await poller.tick();

    expect(producer.sent).toHaveLength(1);
    expect(db.rows[0]!.published_at).toBeInstanceOf(Date);
  });

  it("one failing topic does not block publishes to the others", async () => {
    const ok = makeRow({ topic: "auth.events.v1" });
    const bad = makeRow({ topic: "auth.broken.v1" });
    db.rows.push(ok, bad);
    producer.failingTopics.set("auth.broken.v1", "unknown topic");

    await poller.tick();

    expect(db.rows.find((r) => r.id === ok.id)!.published_at).toBeInstanceOf(
      Date,
    );
    const failedRow = db.rows.find((r) => r.id === bad.id)!;
    expect(failedRow.published_at).toBeNull();
    expect(failedRow.attempts).toBe(1);
    expect(failedRow.last_error).toBe("unknown topic");
    expect(db.committed).toBe(1); // both outcomes land in the one commit
  });

  it("a database failure rolls back and is survivable (next tick retries)", async () => {
    db.rows.push(makeRow());
    db.failNextSelect = true;

    await poller.tick(); // must not throw

    expect(db.rolledBack).toBe(1);
    expect(logger.byLevel("error")[0]!.msg).toContain("outbox tick failed");

    await poller.tick(); // outage over — row goes through untouched
    expect(db.rows[0]!.published_at).toBeInstanceOf(Date);
    expect(db.rows[0]!.attempts).toBe(0);
  });
});
