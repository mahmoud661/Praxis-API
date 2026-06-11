"""
Content references — the side-channel that lets the model mention an
entity inline (via a compact alias like `turn3image1` or a citation
bundle like `citeturn0search2turn0search4`) and have the frontend
render it as a rich UI element (thumbnail, citation pill, etc.) without
polluting the text stream with structured payloads.

The model only ever emits compact text tokens. The heavy data — file
metadata, citation URLs, snippets — lives in a parallel
`content_references: list[ContentReference]` sidecar correlated to the
text by character offsets (`start_idx`, `end_idx`). The frontend walks
those offsets and swaps each span for the appropriate component.

Two mention shapes:

  - Inline alias — a single `turn{N}{category}{M}` token in the model's
    prose (e.g. `turn3image1`). Each one becomes ONE
    `AttachmentReference` (or other typed variant) whose payload carries
    the resolved entity.

  - Citation block — a `cite` prefix followed by one OR MORE concatenated
    aliases (e.g. `citeturn0search2turn0search4turn0news18`). This is
    ONE `CitationReference` whose `items` tuple carries the resolved
    webpage for each bundled alias. The frontend renders a single pill
    that expands to show all three sources.

Categories that participate:

  - attachments: `file`, `image`, `pdf`, `audio`, `video`
  - citations:   `search`, `news`

Categories the scanner doesn't know about (e.g. a hypothetical
`turn3plot1`) are silently ignored — the literal text passes through
unmodified, the frontend renders it as plain prose. Same for aliases
that resolve to nothing (`turn99file1` in a 4-turn thread).

Wire format on the WS message is `content_references: list[dict]` —
each dict is one of the variants below, serialized via `asdict`. The
discriminator field `kind` lets the frontend dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ParsedAlias:
    """One alias as it appeared in text — pre-resolution. The scanner
    produces these; the resolver turns them into a typed
    `ContentReference`. Kept as a separate type so resolution can fail
    (e.g. `turn99file1` when only 3 turns exist) without losing track
    of the literal that needs to survive in the rendered text."""

    matched_text: str
    start_idx: int
    end_idx: int
    turn_index: int
    category: str  # one of the categories listed in the module docstring
    item_index: int


@dataclass(frozen=True, slots=True)
class ParsedCitation:
    """A `cite` prefix plus one or more concatenated aliases. The
    scanner emits ONE of these per `cite...` block; the bundled aliases
    are kept in order so the resolver can preserve the model's
    citation ordering when it builds the `CitationReference.items`."""

    matched_text: str
    start_idx: int
    end_idx: int
    aliases: tuple[ParsedAlias, ...]


# --- resolved payloads (variant-specific) -------------------------------------


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """Payload for a single attachment alias. Carries enough for the
    frontend to render a thumbnail or chip without re-fetching
    metadata. `file_id` is the durable UUID; the alias text
    (`turn3image1`) is captured separately on the outer reference."""

    file_id: str
    filename: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class WebpageRef:
    """One webpage cited in a `cite...` block. Mirrors the subset of
    ChatGPT's `grouped_webpages.items[]` that the frontend actually
    needs to render — title + url + attribution + snippet."""

    title: str
    url: str
    attribution: str | None = None
    snippet: str | None = None


# --- typed references (tagged union via `kind`) -------------------------------


@dataclass(frozen=True, slots=True)
class AttachmentReference:
    """Inline attachment mention. Rendered as a thumbnail or file chip
    at `[start_idx:end_idx]` of the message text."""

    matched_text: str
    start_idx: int
    end_idx: int
    attachment: AttachmentRef
    kind: Literal["attachment"] = "attachment"


@dataclass(frozen=True, slots=True)
class CitationReference:
    """Citation block. ONE rendered pill bundling one or more sources.
    `items` preserves the order the model emitted them in."""

    matched_text: str
    start_idx: int
    end_idx: int
    items: tuple[WebpageRef, ...]
    kind: Literal["citation"] = "citation"


ContentReference = AttachmentReference | CitationReference
"""Tagged union — frontend dispatches on `.kind`. Extend by adding a
new variant dataclass with its own `kind` literal; existing renderers
keep working because unknown kinds fall back to plain text."""


# Category sets are exposed so the scanner / resolver / tests share one
# source of truth. Extend ATTACHMENT_CATEGORIES when a new attachment
# modality lands (e.g. `model3d`); extend CITATION_CATEGORIES if the
# search tool starts emitting new bucket names.

ATTACHMENT_CATEGORIES: frozenset[str] = frozenset(
    {"file", "image", "pdf", "audio", "video"}
)

CITATION_CATEGORIES: frozenset[str] = frozenset({"search", "news"})

ALL_CATEGORIES: frozenset[str] = ATTACHMENT_CATEGORIES | CITATION_CATEGORIES
