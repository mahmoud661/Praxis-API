export const UNIT_OF_WORK = Symbol("UnitOfWork");

// Transactional boundary. The use case asks for a UoW; the adapter decides
// whether that's a SQL transaction, a saga, or no-op (in-memory tests).
export interface UnitOfWork {
  run<T>(work: () => Promise<T>): Promise<T>;
}
