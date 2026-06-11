"""
Port for resolving a parsed content-reference alias to its concrete
payload. The scanner is pure — it only knows shapes (`turn{N}image{M}`)
— so the resolver needs *something* to ask "what's the file id behind
turn 3's first image in this thread?". That something is this port.

Two methods because the return shapes differ (attachments vs webpages)
— a single method that returns a union would force every caller into
isinstance checks. Categories are passed through verbatim so the
implementation can disambiguate `file` vs `image` if it wants to.

Returning `None` is the soft-fail path — the alias was syntactically
valid (turn N's M-th image of that category) but didn't resolve to a
real entity. The service treats that as "skip this alias" and lets the
literal text render as plain prose. We never raise from a lookup.

Implementations live in the infrastructure layer (e.g. backed by the
LangGraph thread state for attachments, or by the search-tool's run
output for citations).
"""

from __future__ import annotations

from typing import Protocol

from ..dtos.content_reference_dto import AttachmentRef, WebpageRef


class IContentReferenceLookup(Protocol):
    async def resolve_attachment(
        self,
        *,
        thread_id: str,
        owner_id: str,
        turn_index: int,
        category: str,
        item_index: int,
    ) -> AttachmentRef | None:
        """Look up the file behind an attachment alias. Returns `None`
        if turn N doesn't exist, has no M-th item of that category, or
        the requesting user can't see it."""

    async def resolve_webpage(
        self,
        *,
        thread_id: str,
        owner_id: str,
        turn_index: int,
        category: str,
        item_index: int,
    ) -> WebpageRef | None:
        """Look up the webpage behind a citation alias. Returns `None`
        if the alias points at something that no longer exists in the
        thread's search/news history."""
