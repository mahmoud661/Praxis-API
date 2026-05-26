import { Response } from "express";
import { Result } from "../../domain/shared/Result";
import {
  ConflictException,
  DomainException,
  NotFoundException,
  UnauthenticatedException,
  ValidationException,
  InvariantViolation,
} from "../../domain/shared/DomainException";

// Central place where domain errors become HTTP status codes. Adding a new
// DomainException only requires one new branch here — controllers stay clean.
export function respondWithResult<T>(
  res: Response,
  result: Result<T, DomainException>,
  successStatus = 200,
): void {
  if (result.isOk()) {
    res.status(successStatus).json(result.getValue());
    return;
  }
  const err = result.getError();
  const status = mapStatus(err);
  res.status(status).json({ error: err.code, message: err.message });
}

function mapStatus(err: DomainException): number {
  if (err instanceof ValidationException) return 400;
  if (err instanceof InvariantViolation) return 400;
  if (err instanceof UnauthenticatedException) return 401;
  if (err instanceof NotFoundException) return 404;
  if (err instanceof ConflictException) return 409;
  return 500;
}
