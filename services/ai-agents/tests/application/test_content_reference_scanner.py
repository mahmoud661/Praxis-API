"""Tests for the content-reference scanner + resolver under
`react_agent/utils/content_reference_scanner.py`.

These functions are the pure core of the content-reference pipeline —
the `ContentReferenceMiddleware` is a thin shim that calls into them
from the agent's `awrap_model_call` hook. We test the pure functions
directly to keep coverage independent of the LangChain middleware
machinery, which lives in a version of `langchain.agents.middleware`
that the local dev env doesn't always have installed.
"""

from __future__ import annotations

import pytest

from app.application.services.agentic.react_agent.references import (
    resolve,
    scan,
)
from app.domain.dtos.content_reference_dto import (
    AttachmentRef,
    AttachmentReference,
    CitationReference,
    WebpageRef,
)


class _FakeLookup:
    """Stand-in for `IContentReferenceLookup`. Caller pre-seeds
    `attachments` and `webpages` keyed by `(turn_index, category,
    item_index)`."""

    def __init__(self) -> None:
        self.attachments: dict[tuple[int, str, int], AttachmentRef] = {}
        self.webpages: dict[tuple[int, str, int], WebpageRef] = {}

    async def resolve_attachment(
        self,
        *,
        thread_id: str,
        owner_id: str,
        turn_index: int,
        category: str,
        item_index: int,
    ) -> AttachmentRef | None:
        return self.attachments.get((turn_index, category, item_index))

    async def resolve_webpage(
        self,
        *,
        thread_id: str,
        owner_id: str,
        turn_index: int,
        category: str,
        item_index: int,
    ) -> WebpageRef | None:
        return self.webpages.get((turn_index, category, item_index))


# ----- pure scan --------------------------------------------------------------


class TestScan:
    def test_no_aliases_returns_empty(self) -> None:
        inline, citations = scan("Just plain text with no references.")
        assert inline == ()
        assert citations == ()

    def test_single_inline_attachment_alias(self) -> None:
        text = "Look at turn3image1, it's a screenshot."
        inline, citations = scan(text)
        assert citations == ()
        assert len(inline) == 1
        alias = inline[0]
        assert alias.matched_text == "turn3image1"
        assert alias.turn_index == 3
        assert alias.category == "image"
        assert alias.item_index == 1
        # offsets bracket the literal in the source text
        assert text[alias.start_idx : alias.end_idx] == "turn3image1"

    def test_multiple_inline_aliases_across_categories(self) -> None:
        text = "First turn0file1, then turn2pdf3 and finally turn5audio2."
        inline, citations = scan(text)
        assert citations == ()
        assert [a.matched_text for a in inline] == [
            "turn0file1",
            "turn2pdf3",
            "turn5audio2",
        ]
        assert [a.category for a in inline] == ["file", "pdf", "audio"]

    def test_unknown_category_does_not_match(self) -> None:
        # `plot` isn't in ALL_CATEGORIES, so `turn3plot1` is plain prose.
        inline, citations = scan("See turn3plot1 for the chart.")
        assert inline == ()
        assert citations == ()

    def test_citation_block_single_alias(self) -> None:
        inline, citations = scan("As shown citeturn0search2 elsewhere.")
        assert inline == ()
        assert len(citations) == 1
        cit = citations[0]
        assert cit.matched_text == "citeturn0search2"
        assert len(cit.aliases) == 1
        assert cit.aliases[0].turn_index == 0
        assert cit.aliases[0].category == "search"
        assert cit.aliases[0].item_index == 2

    def test_citation_block_bundles_multiple_aliases(self) -> None:
        # The exact form seen in the real ChatGPT export.
        text = "According to citeturn0search2turn0search4turn0news18, ..."
        inline, citations = scan(text)
        assert inline == ()
        assert len(citations) == 1
        cit = citations[0]
        assert cit.matched_text == "citeturn0search2turn0search4turn0news18"
        assert [(a.turn_index, a.category, a.item_index) for a in cit.aliases] == [
            (0, "search", 2),
            (0, "search", 4),
            (0, "news", 18),
        ]

    def test_cite_aliases_are_not_double_counted_as_inline(self) -> None:
        # Without reservation the inner aliases would surface twice
        # (once in the cite block, once as inline). Reservation prevents
        # that — only the cite reference should be emitted.
        text = "Mixed citeturn0search2 and turn1image1 inline."
        inline, citations = scan(text)
        assert [a.matched_text for a in inline] == ["turn1image1"]
        assert [c.matched_text for c in citations] == ["citeturn0search2"]

    def test_offsets_in_citation_aliases_point_to_original_text(self) -> None:
        text = "X citeturn0search2turn0search4 Y"
        _inline, citations = scan(text)
        cit = citations[0]
        # Both bundled aliases' offsets index back into `text`.
        for alias in cit.aliases:
            assert text[alias.start_idx : alias.end_idx] == alias.matched_text


# ----- resolver: attachments --------------------------------------------------


class TestResolveAttachment:
    @pytest.mark.asyncio
    async def test_resolves_known_attachment_to_typed_reference(self) -> None:
        lookup = _FakeLookup()
        lookup.attachments[(3, "image", 1)] = AttachmentRef(
            file_id="abc-123",
            filename="login.png",
            mime_type="image/png",
            size_bytes=42_000,
        )
        refs = await resolve(
            text="See turn3image1.",
            lookup=lookup,
            thread_id="t-1",
            owner_id="u-1",
        )
        assert len(refs) == 1
        ref = refs[0]
        assert isinstance(ref, AttachmentReference)
        assert ref.kind == "attachment"
        assert ref.matched_text == "turn3image1"
        assert ref.attachment.file_id == "abc-123"

    @pytest.mark.asyncio
    async def test_unresolved_alias_is_dropped(self) -> None:
        # No seed → lookup returns None → no reference emitted; the
        # literal `turn99file1` survives as plain text on the wire.
        lookup = _FakeLookup()
        refs = await resolve(
            text="Imaginary turn99file1.",
            lookup=lookup,
            thread_id="t-1",
            owner_id="u-1",
        )
        assert refs == []

    @pytest.mark.asyncio
    async def test_loose_citation_alias_outside_cite_is_dropped(self) -> None:
        # `turn0search2` standing alone (no `cite` prefix) has no
        # rendering. The resolver skips it without even consulting the
        # webpage lookup.
        called: list[tuple[int, str, int]] = []

        class _SpyLookup(_FakeLookup):
            async def resolve_webpage(
                self,
                *,
                thread_id: str,
                owner_id: str,
                turn_index: int,
                category: str,
                item_index: int,
            ) -> WebpageRef | None:
                called.append((turn_index, category, item_index))
                return None

        spy = _SpyLookup()
        refs = await resolve(
            text="Stray turn0search2.",
            lookup=spy,
            thread_id="t-1",
            owner_id="u-1",
        )
        assert refs == []
        assert called == []


# ----- resolver: citations ----------------------------------------------------


class TestResolveCitation:
    @pytest.mark.asyncio
    async def test_bundles_multiple_webpages_into_one_reference(self) -> None:
        lookup = _FakeLookup()
        lookup.webpages[(0, "search", 2)] = WebpageRef(
            title="Attention Is All You Need",
            url="https://arxiv.org/abs/1706.03762",
            attribution="arXiv",
        )
        lookup.webpages[(0, "news", 18)] = WebpageRef(
            title="8 Google Employees Invented Modern AI",
            url="https://www.wired.com/story/...",
            attribution="WIRED",
        )
        refs = await resolve(
            text="Per citeturn0search2turn0news18 we know X.",
            lookup=lookup,
            thread_id="t-1",
            owner_id="u-1",
        )
        assert len(refs) == 1
        ref = refs[0]
        assert isinstance(ref, CitationReference)
        assert ref.kind == "citation"
        assert ref.matched_text == "citeturn0search2turn0news18"
        # Order matches the model's emission order.
        assert [item.attribution for item in ref.items] == ["arXiv", "WIRED"]

    @pytest.mark.asyncio
    async def test_citation_with_some_unresolved_items_keeps_the_rest(self) -> None:
        lookup = _FakeLookup()
        lookup.webpages[(0, "search", 2)] = WebpageRef(
            title="OK", url="https://ok.example"
        )
        # `(0, "news", 18)` NOT seeded → that bundled alias is dropped.
        refs = await resolve(
            text="cite citeturn0search2turn0news18 end.",
            lookup=lookup,
            thread_id="t-1",
            owner_id="u-1",
        )
        assert len(refs) == 1
        ref = refs[0]
        assert isinstance(ref, CitationReference)
        assert len(ref.items) == 1
        assert ref.items[0].title == "OK"

    @pytest.mark.asyncio
    async def test_citation_with_zero_resolved_items_is_dropped(self) -> None:
        # Nothing seeded — the whole `cite...` resolves to no items, so
        # we emit no reference and let the literal pass through.
        lookup = _FakeLookup()
        refs = await resolve(
            text="citeturn0search99",
            lookup=lookup,
            thread_id="t-1",
            owner_id="u-1",
        )
        assert refs == []

    @pytest.mark.asyncio
    async def test_attachment_alias_inside_cite_block_is_skipped(self) -> None:
        # A `cite...` is only allowed to bundle citation categories.
        # If the model accidentally splices `turn0image1` into one, the
        # resolver skips that alias but keeps the legitimate ones.
        lookup = _FakeLookup()
        lookup.webpages[(0, "search", 2)] = WebpageRef(
            title="OK", url="https://ok.example"
        )
        refs = await resolve(
            text="citeturn0search2turn0image1",
            lookup=lookup,
            thread_id="t-1",
            owner_id="u-1",
        )
        assert len(refs) == 1
        assert isinstance(refs[0], CitationReference)
        assert len(refs[0].items) == 1
        assert refs[0].items[0].title == "OK"


# ----- output ordering --------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_sorts_references_by_start_idx() -> None:
    lookup = _FakeLookup()
    lookup.attachments[(0, "file", 1)] = AttachmentRef(
        file_id="f-A", filename="a.pdf", mime_type="application/pdf", size_bytes=1
    )
    lookup.webpages[(0, "search", 2)] = WebpageRef(
        title="src", url="https://src.example"
    )
    refs = await resolve(
        text="Use turn0file1 with citeturn0search2.",
        lookup=lookup,
        thread_id="t-1",
        owner_id="u-1",
    )
    assert [r.start_idx for r in refs] == sorted(r.start_idx for r in refs)
