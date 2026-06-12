"""
Content-reference types — re-exported from the react_agent library.

The alias grammar (categories, MIME mapping) and the reference
dataclasses moved into `react_agent.references`: they're owned by the
attachment/citation system that ships with the library, and the
library is the single source of truth so the minting side and the
resolving side can never drift.

This module remains as the app-side import surface (domain code and
tests keep importing from here) — it's a pure re-export. Treat
`react_agent` like any third-party dependency: when it's extracted to
its own package, only the import path on the right changes.
"""

from __future__ import annotations

from ...application.services.agentic.react_agent.references import (
    ALL_CATEGORIES,
    ATTACHMENT_CATEGORIES,
    CITATION_CATEGORIES,
    AttachmentRef,
    AttachmentReference,
    CitationReference,
    ContentReference,
    ParsedAlias,
    ParsedCitation,
    WebpageRef,
    category_for_mime,
)

__all__ = [
    "ALL_CATEGORIES",
    "ATTACHMENT_CATEGORIES",
    "CITATION_CATEGORIES",
    "AttachmentRef",
    "AttachmentReference",
    "CitationReference",
    "ContentReference",
    "ParsedAlias",
    "ParsedCitation",
    "WebpageRef",
    "category_for_mime",
]
