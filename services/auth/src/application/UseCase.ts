import { Result } from "../domain/shared/Result";

// Marker interface. Every use case has a single public `execute` method.
// Forces SRP — if a class wants two operations, it must be two use cases.
export interface UseCase<TInput, TOutput, TError = Error> {
  execute(input: TInput): Promise<Result<TOutput, TError>>;
}
