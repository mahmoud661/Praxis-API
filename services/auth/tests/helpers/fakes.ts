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
  async hash(plain: string): Promise<PasswordHash> {
    return PasswordHash.fromHashedValue("$2b$12$" + plain.padEnd(53, "x"));
  }
  async verify(plain: string, hash: PasswordHash): Promise<boolean> {
    return hash.value === "$2b$12$" + plain.padEnd(53, "x");
  }
}

export class InMemorySessionStore implements SessionStore {
  readonly sessions = new Map<string, SessionData>();
  private counter = 0;
  async create(data: SessionData): Promise<string> {
    const id = `sid-${++this.counter}`;
    this.sessions.set(id, data);
    return id;
  }
  async read(sessionId: string): Promise<SessionData | null> {
    return this.sessions.get(sessionId) ?? null;
  }
  async refresh(): Promise<void> {
    /* no-op */
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

export class CapturingAuditRepo implements IAuditRepo {
  readonly entries: AuditEntryInput[] = [];
  async record(entry: AuditEntryInput): Promise<void> {
    this.entries.push(entry);
  }
}
