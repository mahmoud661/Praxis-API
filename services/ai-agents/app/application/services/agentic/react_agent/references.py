"""
Content references — the side-channel that lets the model mention an
entity inline (via a compact alias like `turn3image1` or a citation
bundle like `citeturn0search2turn0search4`) and have a UI render it as
a rich element (thumbnail, citation pill, …) without polluting the
text stream with structured payloads.

This module is the library's single source of truth for the ALIAS
GRAMMAR: the category sets, the MIME→category mapping, the parsed/
resolved dataclasses, and the scan/resolve pass. The attachment
middlewares MINT aliases with `category_for_mime`; the host's lookup
RESOLVES them through the `ReferenceLookup` port below — both sides
share these definitions, so they cannot drift.

Two mention shapes:

  - Inline alias — a single `turn{N}{category}{M}` token in the model's
    prose (e.g. `turn3image1`). Each becomes ONE `AttachmentReference`
    whose payload carries the resolved entity.

  - Citation block — a `cite` prefix followed by one OR MORE
    concatenated aliases (e.g. `citeturn0search2turn0search4`). ONE
    `CitationReference` whose `items` tuple carries the resolved
    webpage for each bundled alias.

Aliases that don't parse or don't resolve soft-fail: the literal text
passes through as plain prose. No exception ever crosses this module's
boundary from a lookup.

Wire format is `content_references: list[dict]` — each dict is one of
the variants below serialized via `dataclasses.asdict`; the `kind`
field is the frontend's discriminator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol


# ---- categories (grammar) ----------------------------------------------------

# Extend ATTACHMENT_CATEGORIES when a new attachment modality lands
# (e.g. `model3d`); extend CITATION_CATEGORIES if a search tool starts
# emitting new bucket names. Every consumer derives from these — the
# scanner regex, the read_attachment alias parser, the minting side.

ATTACHMENT_CATEGORIES: frozenset[str] = frozenset(
    {"file", "image", "pdf", "audio", "video"}
)

CITATION_CATEGORIES: frozenset[str] = frozenset({"search", "news"})

ALL_CATEGORIES: frozenset[str] = ATTACHMENT_CATEGORIES | CITATION_CATEGORIES


def category_for_mime(mime_type: str) -> str:
    """Map a MIME type to its SPECIFIC attachment category, or `"file"`
    for anything that doesn't match a richer bucket.

    Single source of truth for the MIME→category mapping. Both the
    alias MINTING side (the preload middleware) and the RESOLVING side
    (the host's `ReferenceLookup`) call this, so a minted alias is
    always resolvable by exactly the same rule. `"file"` is also the
    permissive bucket that matches every attachment when used as a
    query category.
    """
    if mime_type.startswith("image/"):
        return "image"
    if mime_type == "application/pdf":
        return "pdf"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "file"


# ---- parsed shapes (pre-resolution) ------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedAlias:
    """One alias as it appeared in text — pre-resolution. The scanner
    produces these; the resolver turns them into a typed
    `ContentReference`. Kept separate so resolution can fail (e.g.
    `turn99file1` in a 4-turn thread) without losing the literal that
    must survive in the rendered text."""

    matched_text: str
    start_idx: int
    end_idx: int
    turn_index: int
    category: str
    item_index: int


@dataclass(frozen=True, slots=True)
class ParsedCitation:
    """A `cite` prefix plus one or more concatenated aliases. ONE per
    `cite…` block; bundled aliases stay in emission order."""

    matched_text: str
    start_idx: int
    end_idx: int
    aliases: tuple[ParsedAlias, ...]


# ---- resolved payloads -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """Payload for a single attachment alias — enough for a UI to
    render a thumbnail or chip without re-fetching metadata."""

    file_id: str
    filename: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class WebpageRef:
    """One cited webpage/document: title + url + attribution + snippet."""

    title: str
    url: str
    attribution: str | None = None
    snippet: str | None = None


# ---- typed references (tagged union via `kind`) -------------------------------


@dataclass(frozen=True, slots=True)
class AttachmentReference:
    """Inline attachment mention rendered at `[start_idx:end_idx]`."""

    matched_text: str
    start_idx: int
    end_idx: int
    attachment: AttachmentRef
    kind: Literal["attachment"] = "attachment"


@dataclass(frozen=True, slots=True)
class CitationReference:
    """Citation block — one pill bundling one or more sources."""

    matched_text: str
    start_idx: int
    end_idx: int
    items: tuple[WebpageRef, ...]
    kind: Literal["citation"] = "citation"


ContentReference = AttachmentReference | CitationReference
"""Tagged union — UIs dispatch on `.kind`. Unknown kinds fall back to
plain text, so adding variants is backward-compatible."""


# ---- lookup port -------------------------------------------------------------


class ReferenceLookup(Protocol):
    """How the library asks the host "what's behind this alias?".

    The scanner is pure — it only knows shapes. The host implements
    this against whatever it persists (thread state, a search-results
    store, a database). Returning `None` is the soft-fail path: the
    alias was syntactically valid but resolves to nothing; the literal
    text renders as prose. Implementations never raise.
    """

    async def resolve_attachment(
        self,
        *,
        thread_id: str,
        owner_id: str,
        turn_index: int,
        category: str,
        item_index: int,
    ) -> AttachmentRef | None: ...

    async def resolve_webpage(
        self,
        *,
        thread_id: str,
        owner_id: str,
        turn_index: int,
        category: str,
        item_index: int,
    ) -> WebpageRef | None: ...


# ---- scanner / resolver ------------------------------------------------------

# One regex covers every alias. The category alternation is anchored to
# the set we actually recognise so a stray `turn3plot1` doesn't tokenize
# — it stays in the prose as plain text.
_CATEGORY_ALTS = "|".join(sorted(ALL_CATEGORIES))
_ALIAS_PATTERN = rf"turn(\d+)({_CATEGORY_ALTS})(\d+)"
_ALIAS_RE = re.compile(_ALIAS_PATTERN)

# Citation: `cite` + one or more aliases, no separator. The +-quantifier
# on the inner group greedily consumes bundled citations
# (`citeturn0search2turn0search4`) as a single match.
_CITE_RE = re.compile(rf"cite((?:{_ALIAS_PATTERN})+)")


def scan(
    text: str,
) -> tuple[tuple[ParsedAlias, ...], tuple[ParsedCitation, ...]]:
    """Walk `text` once, return (inline_aliases, citations).

    Cite blocks are matched first; the character ranges they cover are
    excluded from the inline-alias pass so a bundled alias isn't
    double-counted.
    """
    citations = tuple(_scan_citations(text))
    reserved = sorted(
        ((c.start_idx, c.end_idx) for c in citations), key=lambda p: p[0]
    )
    inline_aliases = tuple(_scan_inline(text, reserved))
    return inline_aliases, citations


async def resolve(
    *,
    text: str,
    lookup: ReferenceLookup,
    thread_id: str,
    owner_id: str,
) -> list[ContentReference]:
    """Scan + resolve. Returns references sorted by `start_idx`.

    Unresolvable aliases are silently dropped — their literal text
    renders as ordinary prose. No exceptions cross this boundary.
    """
    inline_aliases, citations = scan(text)
    refs: list[ContentReference] = []

    for alias in inline_aliases:
        ref = await _resolve_inline_alias(
            lookup=lookup, alias=alias, thread_id=thread_id, owner_id=owner_id
        )
        if ref is not None:
            refs.append(ref)

    for citation in citations:
        ref = await _resolve_citation(
            lookup=lookup,
            citation=citation,
            thread_id=thread_id,
            owner_id=owner_id,
        )
        if ref is not None:
            refs.append(ref)

    refs.sort(key=lambda r: r.start_idx)
    return refs


# ---- private helpers ---------------------------------------------------------


def _scan_citations(text: str):
    for match in _CITE_RE.finditer(text):
        bundled_text = match.group(1)
        # Offsets recorded on each ParsedAlias are absolute (relative
        # to `text`); `bundle_offset` is where the aliases start, just
        # after the literal `cite`.
        bundle_offset = match.start() + len("cite")
        aliases = tuple(
            _alias_from_match(am, base_offset=bundle_offset)
            for am in _ALIAS_RE.finditer(bundled_text)
        )
        yield ParsedCitation(
            matched_text=match.group(0),
            start_idx=match.start(),
            end_idx=match.end(),
            aliases=aliases,
        )


def _scan_inline(text: str, reserved: list[tuple[int, int]]):
    for match in _ALIAS_RE.finditer(text):
        if _overlaps(match.start(), match.end(), reserved):
            continue
        yield _alias_from_match(match, base_offset=0)


async def _resolve_inline_alias(
    *,
    lookup: ReferenceLookup,
    alias: ParsedAlias,
    thread_id: str,
    owner_id: str,
) -> ContentReference | None:
    if alias.category not in ATTACHMENT_CATEGORIES:
        # A loose `turn0search2` outside a `cite…` block has no inline
        # rendering — citations are always wrapped. Drop it; the
        # literal stays in the prose.
        return None
    payload = await lookup.resolve_attachment(
        thread_id=thread_id,
        owner_id=owner_id,
        turn_index=alias.turn_index,
        category=alias.category,
        item_index=alias.item_index,
    )
    if payload is None:
        return None
    return AttachmentReference(
        matched_text=alias.matched_text,
        start_idx=alias.start_idx,
        end_idx=alias.end_idx,
        attachment=payload,
    )


async def _resolve_citation(
    *,
    lookup: ReferenceLookup,
    citation: ParsedCitation,
    thread_id: str,
    owner_id: str,
) -> CitationReference | None:
    items = []
    for alias in citation.aliases:
        if alias.category not in CITATION_CATEGORIES:
            # An attachment alias inside `cite…` is a malformed
            # emission. Skip it but keep the others.
            continue
        payload = await lookup.resolve_webpage(
            thread_id=thread_id,
            owner_id=owner_id,
            turn_index=alias.turn_index,
            category=alias.category,
            item_index=alias.item_index,
        )
        if payload is not None:
            items.append(payload)
    if not items:
        return None
    return CitationReference(
        matched_text=citation.matched_text,
        start_idx=citation.start_idx,
        end_idx=citation.end_idx,
        items=tuple(items),
    )


def _alias_from_match(match: re.Match[str], *, base_offset: int) -> ParsedAlias:
    return ParsedAlias(
        matched_text=match.group(0),
        start_idx=base_offset + match.start(),
        end_idx=base_offset + match.end(),
        turn_index=int(match.group(1)),
        category=match.group(2),
        item_index=int(match.group(3)),
    )


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    """Ranges sorted by start; short-circuit once the next range starts
    past `end`."""
    for rs, re_ in ranges:
        if rs >= end:
            return False
        if re_ > start:
            return True
    return False
