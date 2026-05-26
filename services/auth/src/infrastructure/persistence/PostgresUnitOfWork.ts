import { UnitOfWork } from "../../domain/ports/UnitOfWork";
import { PostgresConnection } from "./PostgresConnection";

// Begins a transaction, sets it as the active client on the connection so
// every repository adapter picks it up, and commits/rolls back at the end.
export class PostgresUnitOfWork implements UnitOfWork {
  constructor(private readonly conn: PostgresConnection) {}

  async run<T>(work: () => Promise<T>): Promise<T> {
    const client = await this.conn.pool.connect();
    try {
      await client.query("BEGIN");
      this.conn.setActiveClient(client);
      const out = await work();
      await client.query("COMMIT");
      return out;
    } catch (err) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw err;
    } finally {
      this.conn.setActiveClient(null);
      client.release();
    }
  }
}
