// Functional Result. Use cases return Result instead of throwing — keeps
// error paths explicit in the type system (no hidden exceptions across
// architectural boundaries).
export class Result<T, E = Error> {
  private constructor(
    private readonly _ok: boolean,
    private readonly _value?: T,
    private readonly _error?: E,
  ) {}

  static ok<T, E = Error>(value: T): Result<T, E> {
    return new Result<T, E>(true, value, undefined);
  }
  static fail<T = never, E = Error>(error: E): Result<T, E> {
    return new Result<T, E>(false, undefined, error);
  }

  isOk(): boolean {
    return this._ok;
  }
  isFail(): boolean {
    return !this._ok;
  }
  getValue(): T {
    if (!this._ok) throw new Error("Result.getValue called on Fail");
    return this._value as T;
  }
  getError(): E {
    if (this._ok) throw new Error("Result.getError called on Ok");
    return this._error as E;
  }
}
