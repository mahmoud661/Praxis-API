"""
Application-layer exceptions. Lives in a `_`-prefixed module so the DI
auto-register globber skips it (it only loads `*.py` whose name doesn't
start with `_`).
"""

from __future__ import annotations


class ThreadNotFoundError(Exception):
    """Raised when a thread doesn't exist OR isn't owned by the caller.
    Controllers map both to 404 — we don't leak existence to other users."""


class MessageNotFoundError(Exception):
    """Raised by TurnsService when the message id referenced by a retry
    or edit isn't found in the thread's current state."""


class InvalidTurnTargetError(Exception):
    """Raised by TurnsService when the caller tries to retry / edit a
    message that isn't a user message (assistant messages can't be
    re-run from directly — find their preceding user message instead)."""


class TurnInProgressError(Exception):
    """Raised by TurnsService when a retry or edit lands while a run is
    already streaming on the thread. The frontend should disable the
    actions while `isStreaming` is true, but this catches races."""
