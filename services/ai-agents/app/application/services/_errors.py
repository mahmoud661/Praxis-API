"""
Application-layer exceptions. Lives in a `_`-prefixed module so the DI
auto-register globber skips it (it only loads `*.py` whose name doesn't
start with `_`).

`FileNotFoundError` / `UnsupportedMimeTypeError` subclass the
react_agent library's error contract (`AttachmentNotFoundError` /
`UnsupportedContentError`): the library's attachment system catches
ITS OWN exception types, and our services satisfy that contract simply
by raising these — no adapters, no re-mapping at the boundary.
"""

from __future__ import annotations

from .agentic.react_agent.ports import (
    AttachmentNotFoundError,
    UnsupportedContentError,
)


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


class FileNotFoundError(AttachmentNotFoundError):  # noqa: A001 — domain term shadows builtin intentionally
    """Raised when a file id doesn't exist OR isn't owned by the
    caller. Controllers map both to 404 — we don't leak existence.
    Subclasses the library's `AttachmentNotFoundError` so the
    react_agent attachment system catches it natively."""


class FileTooLargeError(Exception):
    """Raised when an upload exceeds `MAX_FILE_BYTES`. Controllers map
    to 413 (Payload Too Large) with the cap in the message."""

    def __init__(self, size: int, max_size: int) -> None:
        super().__init__(
            f"file size {size} bytes exceeds limit of {max_size} bytes"
        )
        self.size = size
        self.max_size = max_size


class UnsupportedMimeTypeError(UnsupportedContentError):
    """Raised when an upload's content-type isn't in the platform-wide
    accept list (controllers map to 415), AND by the document extractor
    for MIMEs it can't read. Subclasses the library's
    `UnsupportedContentError` so the react_agent attachment system
    catches it natively."""

    def __init__(self, mime: str) -> None:
        super().__init__(f"unsupported file type: {mime!r}")
        self.mime = mime
