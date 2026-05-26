// Distinct error classes so the presentation layer can map them to HTTP
// without `instanceof` chains everywhere or magic strings.
export abstract class DomainException extends Error {
  abstract readonly code: string;
  constructor(message: string) {
    super(message);
    this.name = this.constructor.name;
  }
}

export class InvariantViolation extends DomainException {
  readonly code = "INVARIANT_VIOLATION";
}
export class ValidationException extends DomainException {
  readonly code = "VALIDATION";
}
export class NotFoundException extends DomainException {
  readonly code = "NOT_FOUND";
}
export class ConflictException extends DomainException {
  readonly code = "CONFLICT";
}
export class UnauthenticatedException extends DomainException {
  readonly code = "UNAUTHENTICATED";
}
