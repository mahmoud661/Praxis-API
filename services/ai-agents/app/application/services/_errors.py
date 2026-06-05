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


class InvalidThreadConfigError(Exception):
    """Raised by ThreadsService.update_config when the caller asks for
    something the agent registry refuses: unknown `agent_id`, override
    on a tool the agent declared as non-toggleable, or override on a
    tool the agent doesn't have at all. Controllers map this to 400
    with the message intact so the frontend can surface it."""


class FileNotFoundError(Exception):  # noqa: A001 — domain term shadows builtin intentionally
    """Raised when a file id doesn't exist OR isn't owned by the
    caller. Controllers map both to 404 — we don't leak existence."""


class FileTooLargeError(Exception):
    """Raised when an upload exceeds `MAX_FILE_BYTES`. Controllers map
    to 413 (Payload Too Large) with the cap in the message."""

    def __init__(self, size: int, max_size: int) -> None:
        super().__init__(
            f"file size {size} bytes exceeds limit of {max_size} bytes"
        )
        self.size = size
        self.max_size = max_size


class UnsupportedMimeTypeError(Exception):
    """Raised when an upload's content-type isn't in the platform-wide
    accept list. Controllers map to 415 (Unsupported Media Type)."""

    def __init__(self, mime: str) -> None:
        super().__init__(f"unsupported file type: {mime!r}")
        self.mime = mime
