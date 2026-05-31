import "reflect-metadata";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

// IMPORTANT: every import below must come AFTER the global-setup file
// has run (vitest guarantees this). They pull `loadEnv()` transitively
// via data-source.ts, which exits the process if DATABASE_URL is
// missing — global-setup sets it before module evaluation.
import { AppDataSource } from "../src/infrastructure/database/data-source";
import { UserRepo } from "../src/infrastructure/database/repos/UserRepo";
import { AuditRepo } from "../src/infrastructure/database/repos/AuditRepo";
import { OutboxRepo } from "../src/infrastructure/database/repos/OutboxRepo";
import { TypeOrmUnitOfWork } from "../src/infrastructure/database/TypeOrmUnitOfWork";
import { User } from "../src/domain/entities/user.entity";
import { AuditLog } from "../src/domain/entities/audit-log.entity";
import { OutboxEvent } from "../src/domain/entities/outbox.entity";
import { Email } from "../src/domain/value-objects/Email";
import { PasswordHash } from "../src/domain/value-objects/PasswordHash";
import { UserId } from "../src/domain/value-objects/UserId";
import { ConflictException } from "../src/domain/shared/DomainException";

// A plausible-shape bcrypt hash. We don't verify hashes here (that's a
// unit test for the BcryptPasswordHasher); the repo only cares that the
// value validates against PasswordHash.fromHashedValue() and round-trips.
const TEST_HASH =
  "$2b$12$abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUV";

function makeUser(emailRaw: string): User {
  return User.register({
    id: UserId.generate(),
    email: Email.create(emailRaw),
    passwordHash: PasswordHash.fromHashedValue(TEST_HASH),
  });
}

beforeAll(async () => {
  // `synchronize: true` (set by data-source for non-production NODE_ENV)
  // creates the schema from the entity definitions on first init. We
  // don't ship migrations for this service, so this IS the schema
  // bootstrap.
  if (!AppDataSource.isInitialized) {
    await AppDataSource.initialize();
  }
});

afterAll(async () => {
  if (AppDataSource.isInitialized) {
    await AppDataSource.destroy();
  }
});

beforeEach(async () => {
  // Wipe between tests so order doesn't matter. RESTART IDENTITY resets
  // any sequences; CASCADE handles cross-table FKs (we don't have any
  // today, but it's free defence).
  await AppDataSource.query(
    `TRUNCATE TABLE users, audit_log, outbox RESTART IDENTITY CASCADE`,
  );
});

describe("UserRepo — round-trip", () => {
  it("inserts a user and finds it by id and by email", async () => {
    const repo = new UserRepo();
    const user = makeUser("alice@example.com");
    await repo.save(user);

    const byId = await repo.findById(UserId.from(user.id));
    expect(byId).not.toBeNull();
    expect(byId?.email).toBe("alice@example.com");
    expect(byId?.passwordHash).toBe(TEST_HASH);
    expect(byId?.roles).toEqual(["user"]);

    const byEmail = await repo.findByEmail(Email.create("alice@example.com"));
    expect(byEmail?.id).toBe(user.id);
  });

  it("returns null on misses", async () => {
    const repo = new UserRepo();
    expect(
      await repo.findByEmail(Email.create("ghost@example.com")),
    ).toBeNull();
    expect(await repo.findById(UserId.generate())).toBeNull();
  });

  it("findByEmail uses the normalized (lowercased, trimmed) value", async () => {
    // Email.create normalizes inputs before storing — verify that round-
    // trip lookups via the same value object hit the row regardless of
    // the input casing the caller used.
    const repo = new UserRepo();
    await repo.save(makeUser("Mixed.Case@Example.COM"));

    const hit = await repo.findByEmail(Email.create("  mixed.case@example.com  "));
    expect(hit).not.toBeNull();
  });

  it("existsByEmail tracks state", async () => {
    const repo = new UserRepo();
    const email = Email.create("x@x.com");
    expect(await repo.existsByEmail(email)).toBe(false);
    await repo.save(makeUser("x@x.com"));
    expect(await repo.existsByEmail(email)).toBe(true);
  });

  it("translates the email-unique driver error into ConflictException", async () => {
    // The unique index is the real guarantor for race-condition signups —
    // the service's existsByEmail() pre-check runs outside the tx and
    // can race. The repo must translate Postgres's 23505 into the
    // domain exception so the API surfaces it as 409 (not a 500).
    const repo = new UserRepo();
    await repo.save(makeUser("dup@example.com"));
    await expect(repo.save(makeUser("dup@example.com"))).rejects.toBeInstanceOf(
      ConflictException,
    );
  });
});

describe("OutboxRepo — round-trip", () => {
  it("inserts a batch of rows with payload + headers preserved", async () => {
    const repo = new OutboxRepo();
    await repo.add([
      {
        aggregateId: "u-1",
        topic: "auth.user",
        eventName: "UserRegistered",
        payload: { userId: "u-1", email: "a@b.com" },
        headers: { traceId: "trace-1" },
      },
      {
        aggregateId: "u-2",
        topic: "auth.user",
        eventName: "UserRegistered",
        payload: { userId: "u-2", email: "c@d.com" },
        headers: {},
      },
    ]);

    const rows = await AppDataSource.getRepository(OutboxEvent).find({
      order: { aggregateId: "ASC" },
    });
    expect(rows).toHaveLength(2);
    expect(rows[0]?.aggregateId).toBe("u-1");
    expect(rows[0]?.payload).toEqual({ userId: "u-1", email: "a@b.com" });
    expect(rows[0]?.headers).toEqual({ traceId: "trace-1" });
    // The poller picks rows where published_at IS NULL — new rows must
    // start in that state.
    expect(rows.every((r) => r.publishedAt === null)).toBe(true);
    expect(rows.every((r) => r.createdAt instanceof Date)).toBe(true);
  });

  it("is a no-op for an empty batch (no row inserted, no query sent)", async () => {
    const repo = new OutboxRepo();
    await repo.add([]);
    expect(await AppDataSource.getRepository(OutboxEvent).count()).toBe(0);
  });
});

describe("AuditRepo — round-trip", () => {
  it("records an entry with sensible defaults for optional fields", async () => {
    // `actor_id` is typed `uuid` in Postgres — actorId always comes
    // from User.id in production, which is itself a UUID. Pass a
    // proper UUID here so the test exercises the real path.
    const actor = UserId.generate().value;
    const repo = new AuditRepo();
    await repo.record({ action: "signup", actorId: actor });

    const rows = await AppDataSource.getRepository(AuditLog).find();
    expect(rows).toHaveLength(1);
    const row = rows[0]!;
    expect(row.action).toBe("signup");
    expect(row.actorId).toBe(actor);
    expect(row.targetId).toBeNull();
    expect(row.details).toEqual({});
    expect(row.ip).toBeNull();
    expect(row.occurredAt).toBeInstanceOf(Date);
  });

  it("preserves provided ip + details + targetId", async () => {
    const repo = new AuditRepo();
    await repo.record({
      action: "login.failed",
      actorId: null,
      targetId: "user-42",
      details: { reason: "wrong-password", attempts: 3 },
      ip: "203.0.113.42",
    });

    const row = (await AppDataSource.getRepository(AuditLog).find())[0]!;
    expect(row.targetId).toBe("user-42");
    expect(row.details).toEqual({ reason: "wrong-password", attempts: 3 });
    expect(row.ip).toBe("203.0.113.42");
  });
});

describe("TypeOrmUnitOfWork — transactional atomicity", () => {
  it("commits user + outbox + audit together when work succeeds", async () => {
    const userRepo = new UserRepo();
    const outboxRepo = new OutboxRepo();
    const auditRepo = new AuditRepo();
    const uow = new TypeOrmUnitOfWork();

    await uow.run(async () => {
      const u = makeUser("commit@example.com");
      await userRepo.save(u);
      await outboxRepo.add([
        {
          aggregateId: u.id,
          topic: "auth.user",
          eventName: "UserRegistered",
          payload: { userId: u.id },
          headers: {},
        },
      ]);
      await auditRepo.record({ action: "signup", actorId: u.id });
    });

    // All three writes survived the commit.
    expect(await AppDataSource.getRepository(User).count()).toBe(1);
    expect(await AppDataSource.getRepository(OutboxEvent).count()).toBe(1);
    expect(await AppDataSource.getRepository(AuditLog).count()).toBe(1);
  });

  it("rolls back everything when work throws AFTER successful writes", async () => {
    // The defining contract for the outbox pattern: if the business
    // write succeeds but the work block fails afterwards, NEITHER the
    // business row NOR the outbox row may survive — otherwise downstream
    // sees an event for state that doesn't exist.
    const userRepo = new UserRepo();
    const outboxRepo = new OutboxRepo();
    const uow = new TypeOrmUnitOfWork();

    await expect(
      uow.run(async () => {
        await userRepo.save(makeUser("rollback@example.com"));
        await outboxRepo.add([
          {
            aggregateId: "u",
            topic: "t",
            eventName: "e",
            payload: {},
            headers: {},
          },
        ]);
        throw new Error("boom");
      }),
    ).rejects.toThrow("boom");

    expect(await AppDataSource.getRepository(User).count()).toBe(0);
    expect(await AppDataSource.getRepository(OutboxEvent).count()).toBe(0);
  });

  it("rolls back when the second write triggers a unique-constraint error", async () => {
    // Realistic failure path: two concurrent signups slip past
    // existsByEmail and hit the unique index inside the tx. The first
    // write inside the same UoW must also disappear, leaving the table
    // empty.
    const userRepo = new UserRepo();
    const uow = new TypeOrmUnitOfWork();

    // Seed an existing user so the second write in the UoW will conflict.
    await userRepo.save(makeUser("race@example.com"));
    expect(await AppDataSource.getRepository(User).count()).toBe(1);

    await expect(
      uow.run(async () => {
        // This first save would have succeeded on its own…
        await userRepo.save(makeUser("companion@example.com"));
        // …but this second one conflicts, rolling the whole tx back.
        await userRepo.save(makeUser("race@example.com"));
      }),
    ).rejects.toBeInstanceOf(ConflictException);

    // Only the pre-seeded row survives; the "companion" write was rolled
    // back even though it didn't itself fail.
    const remaining = await AppDataSource.getRepository(User).find();
    expect(remaining).toHaveLength(1);
    expect(remaining[0]?.email).toBe("race@example.com");
  });

  it("repos outside any UoW use the default DataSource (auto-commit)", async () => {
    // Sanity-check the tx-context branch: calling save() without a
    // surrounding uow.run() still writes (no implicit transaction
    // required for a single statement).
    const userRepo = new UserRepo();
    await userRepo.save(makeUser("autocommit@example.com"));
    expect(await AppDataSource.getRepository(User).count()).toBe(1);
  });
});
