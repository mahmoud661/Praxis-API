"""
Pure scanner + resolver for content-reference aliases in an assistant
message.

This is the engine behind `ContentReferenceMiddleware` (under
`react_agent/middlewares/`). Lives one level up — outside the
`react_agent/` package — for two reasons:

  1. It's pure Python (regex + a port call). It has no dependency on
     LangChain or LangGraph, and shouldn't pay the cost of being pulled
     through `react_agent/__init__.py`'s graph-builder import chain on
     every load.

  2. A future caller that needs the same scan (analytics, debug
     tooling, an alternate transport) can import these functions
     directly without dragging in the agent runtime.

Public API:

  - `scan(text)` — one regex pass, returns inline aliases + citation
    blocks. Cite blocks are matched first so their character ranges
    can be reserved against the inline pass.

  - `resolve(text, lookup, thread_id, owner_id)` — scans, then resolves
    each parsed alias via the `IContentReferenceLookup` port. Soft-
    fails throughout: aliases that don't resolve are silently dropped
    (their literal text passes through as prose), citation blocks that
    resolve to zero items are also dropped, and attachment aliases
    mistakenly bundled inside a `cite...` are skipped without breaking
    the rest of the bundle.

File layout: module constants → public API → private helpers, in that
order. No interleaving — read top-to-bottom and you walk from "what
shapes do we recognise" → "what callers invoke" → "how each piece
works under the hood".
"""

from __future__ import annotations

import re

from ....domain.dtos.content_reference_dto import (
    ALL_CATEGORIES,
    ATTACHMENT_CATEGORIES,
    CITATION_CATEGORIES,
    AttachmentReference,
    CitationReference,
    ContentReference,
    ParsedAlias,
    ParsedCitation,
)
from ....domain.ports.content_reference_lookup import IContentReferenceLookup


# ---- module constants --------------------------------------------------------

# One regex covers every alias. The category alternation is anchored to
# the set we actually recognise so a stray `turn3plot1` doesn't tokenize
# — it stays in the prose as plain text. New categories slot in by
# extending `ALL_CATEGORIES` (frozen set in the DTO module).
_CATEGORY_ALTS = "|".join(sorted(ALL_CATEGORIES))
_ALIAS_PATTERN = rf"turn(\d+)({_CATEGORY_ALTS})(\d+)"
_ALIAS_RE = re.compile(_ALIAS_PATTERN)

# Citation: `cite` + one or more aliases, no separator. The +-quantifier
# on the inner group is what lets the regex greedily consume bundled
# citations (`citeturn0search2turn0search4turn0news18`) as a single
# match instead of three separate cite blocks.
_CITE_RE = re.compile(rf"cite((?:{_ALIAS_PATTERN})+)")


# ---- public api --------------------------------------------------------------


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
    lookup: IContentReferenceLookup,
    thread_id: str,
    owner_id: str,
) -> list[ContentReference]:
    """Scan + resolve. Returns references sorted by `start_idx`.

    Unresolvable aliases are silently dropped — the frontend renders
    their literal text as ordinary prose. No exceptions cross this
    boundary; every soft-fail path returns an empty list at worst.
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
        # The match offsets we record on each `ParsedAlias` below are
        # relative to `text`, not to the bundle — `bundle_offset` is
        # the absolute position where the aliases start (just after the
        # literal `cite`).
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
    lookup: IContentReferenceLookup,
    alias: ParsedAlias,
    thread_id: str,
    owner_id: str,
) -> ContentReference | None:
    if alias.category not in ATTACHMENT_CATEGORIES:
        # A loose `turn0search2` outside a `cite...` block has no inline
        # rendering — citations are always wrapped. Drop it; the literal
        # stays in the prose.
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
    lookup: IContentReferenceLookup,
    citation: ParsedCitation,
    thread_id: str,
    owner_id: str,
) -> CitationReference | None:
    items = []
    for alias in citation.aliases:
        if alias.category not in CITATION_CATEGORIES:
            # A citation block is only allowed to bundle citation
            # categories; an attachment alias inside `cite...` is a
            # malformed emission. Skip it but keep the others.
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
    turn_index = int(match.group(1))
    category = match.group(2)
    item_index = int(match.group(3))
    return ParsedAlias(
        matched_text=match.group(0),
        start_idx=base_offset + match.start(),
        end_idx=base_offset + match.end(),
        turn_index=turn_index,
        category=category,
        item_index=item_index,
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
