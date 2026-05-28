import { AsyncLocalStorage } from "async_hooks";
import { EntityManager } from "typeorm";

// Holds the EntityManager of the transaction currently in flight (if any),
// scoped to the async call chain. The UnitOfWork sets it around a transaction;
// repositories read it so all their writes join that single transaction —
// this is what makes the user row + outbox row commit atomically.
const storage = new AsyncLocalStorage<EntityManager>();

export function runWithManager<T>(
  manager: EntityManager,
  fn: () => Promise<T>,
): Promise<T> {
  return storage.run(manager, fn);
}

// The active transactional manager, or undefined when not inside a UoW.
export function getManager(): EntityManager | undefined {
  return storage.getStore();
}
