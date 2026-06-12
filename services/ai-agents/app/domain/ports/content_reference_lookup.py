"""
App-side port for the react_agent library's `ReferenceLookup`.

The protocol's SHAPE is defined BY the library (`react_agent.references`)
— it's what the library's content-reference system asks its host to
provide. This module keeps the app's established port name
(`IContentReferenceLookup`) as a Protocol subclass rather than a bare
alias: the DI container resolves constructor annotations by
`__name__`, so the name must survive ("IContentReferenceLookup", not
"ReferenceLookup"). Structurally the two are identical — anything
satisfying one satisfies the other.

Returning `None` from either method is the soft-fail path — the alias
was syntactically valid but didn't resolve; the literal text renders
as plain prose. Implementations never raise.
"""

from __future__ import annotations

from typing import Protocol

from ...application.services.agentic.react_agent.references import (
    ReferenceLookup,
)


class IContentReferenceLookup(ReferenceLookup, Protocol):
    """The app's name for the library's port — see module docstring."""


__all__ = ["IContentReferenceLookup"]
