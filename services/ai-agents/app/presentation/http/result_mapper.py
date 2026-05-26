from __future__ import annotations

from fastapi import HTTPException

from ...domain.shared.exceptions import (
    ConflictException,
    DomainException,
    InvariantViolation,
    NotFoundException,
    UnauthenticatedException,
    ValidationException,
)


def _status_for(err: DomainException) -> int:
    if isinstance(err, ValidationException):
        return 400
    if isinstance(err, InvariantViolation):
        return 400
    if isinstance(err, UnauthenticatedException):
        return 401
    if isinstance(err, NotFoundException):
        return 404
    if isinstance(err, ConflictException):
        return 409
    return 500


def raise_for_error(err: DomainException) -> None:
    """Translate a domain error into an HTTP exception. Controllers call
    this so they don't repeat status-code switches."""
    raise HTTPException(
        status_code=_status_for(err),
        detail={"error": err.code, "message": err.message},
    )
