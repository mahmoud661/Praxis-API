// In-memory test doubles for every dependency the AuthService needs. Because
// the service depends only on interfaces, it runs in isolation — no Postgres,
// no Redis, no Kafka in unit tests.

import { User } from "../../src/domain/entities/user.entity";
import { Email } from "../../src/domain/value-objects/Email";
import { PasswordHash } from "../../src/domain/value-objects/PasswordHash";
import { UserId } from "../../src/domain/value-objects/UserId";
import { IUserRepo } from "../../src/domain/IRepos/IUserRepo";
import {
  AuditEntryInput,
  IAuditRepo,
} from "../../src/domain/IRepos/IAuditRepo";
import { PasswordHasher } from "../../src/domain/ports/PasswordHasher";
import {
  SessionData,
  SessionStore,
} from "../../src/domain/ports/SessionStore";
import { EventPublisher } from "../../src/domain/ports/EventPublisher";
import { UnitOfWork } from "../../src/domain/ports/UnitOfWork";
import { Logger } from "../../src/domain/ports/Logger";
import { LoginAttemptTracker } from "../../src/domain/ports/LoginAttemptTracker";
import { DomainEvent } from "../../src/domain/shared/DomainEvent";

export class InMemoryUserRepo implements IUserRepo {
  readonly byId = new Map<string, User>();

  async save(user: User): Promise<void> {
    this.byId.set(user.id, user);
  }
  async findById(id: UserId): Promise<User | null> {
    return this.byId.get(id.value) ?? null;
  }
  async findByEmail(email: Email): Promise<User | null> {
    for (const u of this.byId.values()) if (u.email === email.value) return u;
    return null;
  }
  async existsByEmail(email: Email): Promise<boolean> {
    return (await this.findByEmail(email)) !== null;
  }
}

// Deterministic hasher — DOES NOT use bcrypt so tests are fast.
export class StubPasswordHasher implements PasswordHasher {
  verifyCalls = 0;
  async hash(plain: string): Promise<PasswordHash> {
    return PasswordHash.fromHashedValue("$2b$12$" + plain.padEnd(53, "x"));
  }
  async verify(plain: string, hash: PasswordHash): Promise<boolean> {
    this.verifyCalls += 1;
    return hash.value === "$2b$12$" + plain.padEnd(53, "x");
  }
}

export class InMemorySessionStore implements SessionStore {
  readonly sessions = new Map<string, SessionData>();
  readonly refreshed: string[] = [];
  private counter = 0;
  async create(data: SessionData): Promise<string> {
    const id = `sid-${++this.counter}`;
    this.sessions.set(id, data);
    return id;
  }
  async read(sessionId: string): Promise<SessionData | null> {
    return this.sessions.get(sessionId) ?? null;
  }
  async refresh(sessionId: string): Promise<void> {
    this.refreshed.push(sessionId);
  }
  async destroy(sessionId: string): Promise<void> {
    this.sessions.delete(sessionId);
  }
}

export class CapturingEventPublisher implements EventPublisher {
  readonly published: { topic: string; events: ReadonlyArray<DomainEvent> }[] =
    [];
  shouldFail = false;
  async publish(
    topic: string,
    events: ReadonlyArray<DomainEvent>,
  ): Promise<void> {
    if (this.shouldFail) throw new Error("simulated publish failure");
    this.published.push({ topic, events });
  }
}

// Just runs the callback — no transaction. Tests assert on repo state after
// `run` returns, which is the same boundary as prod.
export class NoOpUnitOfWork implements UnitOfWork {
  async run<T>(work: () => Promise<T>): Promise<T> {
    return work();
  }
}

export class SilentLogger implements Logger {
  debug(): void {}
  info(): void {}
  warn(): void {}
  error(): void {}
}

// Records every log call so tests can assert on level + message + context
// (e.g. "audit failures are warned about, with the action name").
export class CapturingLogger implements Logger {
  readonly logs: {
    level: "debug" | "info" | "warn" | "error";
    msg: string;
    ctx?: Record<string, unknown>;
  }[] = [];
  debug(msg: string, ctx?: Record<string, unknown>): void {
    this.logs.push({ level: "debug", msg, ctx });
  }
  info(msg: string, ctx?: Record<string, unknown>): void {
    this.logs.push({ level: "info", msg, ctx });
  }
  warn(msg: string, ctx?: Record<string, unknown>): void {
    this.logs.push({ level: "warn", msg, ctx });
  }
  error(msg: string, ctx?: Record<string, unknown>): void {
    this.logs.push({ level: "error", msg, ctx });
  }
  byLevel(level: "debug" | "info" | "warn" | "error") {
    return this.logs.filter((l) => l.level === level);
  }
}

export class CapturingAuditRepo implements IAuditRepo {
  readonly entries: AuditEntryInput[] = [];
  shouldFail = false;
  async record(entry: AuditEntryInput): Promise<void> {
    if (this.shouldFail) throw new Error("simulated audit outage");
    this.entries.push(entry);
  }
}

// In-memory mirror of the lockout policy: counter per email, locked once the
// counter reaches `maxFailures`. No TTL — unit tests never wait on clocks.
export class FakeLoginAttemptTracker implements LoginAttemptTracker {
  readonly failures = new Map<string, number>();
  maxFailures = 5;
  async recordFailure(email: Email): Promise<number> {
    const next = (this.failures.get(email.value) ?? 0) + 1;
    this.failures.set(email.value, next);
    return next;
  }
  async isLocked(email: Email): Promise<boolean> {
    return (this.failures.get(email.value) ?? 0) >= this.maxFailures;
  }
  async reset(email: Email): Promise<void> {
    this.failures.delete(email.value);
  }
}
