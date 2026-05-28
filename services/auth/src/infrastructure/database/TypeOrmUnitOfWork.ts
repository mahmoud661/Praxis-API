import { injectable } from "tsyringe";
import { UnitOfWork } from "../../domain/ports/UnitOfWork";
import { AppDataSource } from "./data-source";
import { runWithManager } from "./tx-context";

// Runs `work` inside a single TypeORM transaction and publishes the
// transaction's EntityManager to the async-local context, so every repository
// touched during `work` joins the same transaction (commit/rollback together).
@injectable()
export class TypeOrmUnitOfWork implements UnitOfWork {
  run<T>(work: () => Promise<T>): Promise<T> {
    return AppDataSource.transaction((manager) =>
      runWithManager(manager, work),
    );
  }
}
