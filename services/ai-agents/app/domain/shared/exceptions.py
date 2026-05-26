from __future__ import annotations


class DomainException(Exception):
    """Base class for all domain errors. `code` is consumed by the
    presentation layer to map onto HTTP statuses without isinstance chains."""

    code: str = "DOMAIN"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationException(DomainException):
    code = "VALIDATION"


class InvariantViolation(DomainException):
    code = "INVARIANT_VIOLATION"


class NotFoundException(DomainException):
    code = "NOT_FOUND"


class ConflictException(DomainException):
    code = "CONFLICT"


class UnauthenticatedException(DomainException):
    code = "UNAUTHENTICATED"
