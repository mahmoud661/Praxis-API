import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { PostgreSqlContainer, StartedPostgreSqlContainer } from "@testcontainers/postgresql";
import { PostgresConnection } from "../src/infrastructure/persistence/PostgresConnection";
import { PostgresUnitOfWork } from "../src/infrastructure/persistence/PostgresUnitOfWork";
import { PostgresUserRepository } from "../src/infrastructure/persistence/PostgresUserRepository";
import { OutboxEventPublisher } from "../src/infrastructure/messaging/OutboxEventPublisher";
import { runMigrations } from "../src/infrastructure/persistence/migrations";
import { User } from "../src/domain/entities/User";
import { Email } from "../src/domain/value-objects/Email";
import { PasswordHash } from "../src/domain/value-objects/PasswordHash";
import { UserId } from "../src/domain/value-objects/UserId";

// Real-Postgres tests that prove the adapters honour their port contracts.
// Spins up a single container for the file (beforeAll) and truncates
// per-test (beforeEach) for isolation.

let pg: StartedPostgreSqlContainer;
let conn: PostgresConnection;

beforeAll(async () => {
  pg = await new PostgreSqlContainer("postgres:16-alpine")
    .withDatabase("auth")
    .withUsername("auth")
    .withPassword("auth")
    .start();
  conn = new PostgresConnection(pg.getConnectionUri());
  await runMigrations(conn);
}, 120_000);

afterAll(async () => {
  await conn?.close().catch(() => undefined);
  await pg?.stop().catch(() => undefined);
});

beforeEach(async () => {
  await conn.pool.query("TRUNCATE users, outbox, audit_log RESTART IDENTITY CASCADE");
});

const okHash = "$2b$12$" + "a".repeat(53);

describe("PostgresUserRepository", () => {
  it("round-trips a user including roles", async () => {
    const repo = new PostgresUserRepository(conn);
    const user = User.register({
      id: UserId.generate(),
      email: Email.create("integration@example.com"),
      passwordHash: PasswordHash.fromHashedValue(okHash),
      roles: ["user", "admin"],
    });
    await repo.save(user);

    const loaded = await repo.findById(user.id);
    expect(loaded).not.toBeNull();
    expect(loaded!.email.value).toBe("integration@example.com");
    expect(loaded!.roles).toEqual(["user", "admin"]);
    expect(loaded!.hasRole("admin")).toBe(true);
  });

  it("existsByEmail and findByEmail use the unique index", async () => {
    const repo = new PostgresUserRepository(conn);
    const email = Email.create("only@example.com");
    expect(await repo.existsByEmail(email)).toBe(false);
    await repo.save(
      User.register({
        id: UserId.generate(),
        email,
        passwordHash: PasswordHash.fromHashedValue(okHash),
      }),
    );
    expect(await repo.existsByEmail(email)).toBe(true);
    expect((await repo.findByEmail(email))!.email.value).toBe("only@example.com");
  });
});

describe("Transactional outbox", () => {
  it("rolls back the user row when the outbox write fails", async () => {
    const uow = new PostgresUnitOfWork(conn);
    const repo = new PostgresUserRepository(conn);
    const publisher = new OutboxEventPublisher(conn);

    // Force the outbox write to fail by inserting a row that violates the
    // payload column (passing a non-JSON value through is hard; easier:
    // run a deliberately bad UPDATE inside the same transaction).
    await expect(
      uow.run(async () => {
        await repo.save(
          User.register({
            id: UserId.generate(),
            email: Email.create("rollback@example.com"),
            passwordHash: PasswordHash.fromHashedValue(okHash),
          }),
        );
        // Send a deliberately broken SQL through the active client to make
        // the transaction fail; the prior INSERT must be rolled back.
        await conn.exec().query("INSERT INTO outbox (aggregate_id, topic, event_name, payload) VALUES ($1, $2, $3, $4)", [
          "no-jsonb-here",
          "auth.events.v1",
          "BadEvent",
          // pg client will treat this as text, but the column is jsonb → invalid JSON.
          "{not-json",
        ]);
        // Use publisher too so the file imports it.
        await publisher.publish("auth.events.v1", []);
      }),
    ).rejects.toBeDefined();

    const { rows } = await conn.pool.query(
      "SELECT 1 FROM users WHERE email = 'rollback@example.com'",
    );
    expect(rows).toHaveLength(0);
  });

  it("commits both rows together on success", async () => {
    const uow = new PostgresUnitOfWork(conn);
    const repo = new PostgresUserRepository(conn);
    const publisher = new OutboxEventPublisher(conn);

    await uow.run(async () => {
      const user = User.register({
        id: UserId.generate(),
        email: Email.create("ok@example.com"),
        passwordHash: PasswordHash.fromHashedValue(okHash),
      });
      await repo.save(user);
      await publisher.publish("auth.events.v1", user.pullEvents());
    });

    const { rows: u } = await conn.pool.query(
      "SELECT 1 FROM users WHERE email = 'ok@example.com'",
    );
    const { rows: o } = await conn.pool.query(
      "SELECT topic, event_name, published_at FROM outbox WHERE event_name = 'UserRegistered'",
    );
    expect(u).toHaveLength(1);
    expect(o).toHaveLength(1);
    expect(o[0].topic).toBe("auth.events.v1");
    expect(o[0].published_at).toBeNull(); // not yet shipped by the poller
  });
});
