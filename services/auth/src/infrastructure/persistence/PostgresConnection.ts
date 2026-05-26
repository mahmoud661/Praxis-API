import { Pool, PoolClient } from "pg";

// Thin wrapper exposing both pool-level queries and transactional client
// acquisition. The UnitOfWork adapter (below) is what use cases actually
// touch — this stays internal to infrastructure.
export class PostgresConnection {
  readonly pool: Pool;
  readonly url: string;
  // Tracks the active transactional client per async context. Set by the
  // UnitOfWork; read by repository adapters so a single transaction binds
  // all repository writes within a use case.
  private activeClient: PoolClient | null = null;

  constructor(url: string) {
    this.url = url;
    this.pool = new Pool({ connectionString: url, max: 10 });
  }

  setActiveClient(c: PoolClient | null): void {
    this.activeClient = c;
  }

  getActiveClient(): PoolClient | null {
    return this.activeClient;
  }

  // Returns either the in-flight transaction client or the pool itself.
  // Repositories should always go through here.
  exec(): Pool | PoolClient {
    return this.activeClient ?? this.pool;
  }

  async close(): Promise<void> {
    await this.pool.end();
  }
}
