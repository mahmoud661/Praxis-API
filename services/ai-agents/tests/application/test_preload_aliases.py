"""Unit tests for the preload middleware's inline-alias minting.

The minted alias MUST resolve through `ContentReferenceLookupService`
exactly as written, so the counter semantics mirror the lookup's
category filter: `image` counts image MIMEs only, `pdf` counts
application/pdf only, and `file` is the permissive bucket counting
EVERY attachment.

This module imports the middleware, which needs `langchain.agents` —
it collects in the Docker build stage (like test_middleware_imports)
and may error locally; Docker pytest is the gate.
"""

from __future__ import annotations

from app.application.services.agentic.react_agent.middlewares.attachment_preload_middleware import (  # noqa: E501
    _next_alias,
    _with_alias_header,
)


class TestNextAlias:
    def test_categories_count_independently(self) -> None:
        counters: dict[str, int] = {}
        assert (
            _next_alias(mime_type="image/png", counters=counters, turn_index=0)
            == "turn0image1"
        )
        assert (
            _next_alias(
                mime_type="application/pdf", counters=counters, turn_index=0
            )
            == "turn0pdf1"
        )
        assert (
            _next_alias(mime_type="image/jpeg", counters=counters, turn_index=0)
            == "turn0image2"
        )

    def test_file_bucket_counts_every_attachment(self) -> None:
        # The lookup's `file` category matches ANY mime, so the 3rd
        # attachment overall is file3 even if the first two were a pdf
        # and an image.
        counters: dict[str, int] = {}
        _next_alias(mime_type="application/pdf", counters=counters, turn_index=2)
        _next_alias(mime_type="image/png", counters=counters, turn_index=2)
        assert (
            _next_alias(mime_type="text/csv", counters=counters, turn_index=2)
            == "turn2file3"
        )

    def test_turn_index_lands_in_alias(self) -> None:
        assert (
            _next_alias(mime_type="text/plain", counters={}, turn_index=5)
            == "turn5file1"
        )

    def test_audio_and_video_get_their_own_categories(self) -> None:
        counters: dict[str, int] = {}
        assert (
            _next_alias(mime_type="audio/mpeg", counters=counters, turn_index=0)
            == "turn0audio1"
        )
        assert (
            _next_alias(mime_type="video/mp4", counters=counters, turn_index=0)
            == "turn0video1"
        )
        # The permissive `file` bucket counted both, so a binary that
        # follows is file3.
        assert (
            _next_alias(
                mime_type="application/zip", counters=counters, turn_index=0
            )
            == "turn0file3"
        )


class TestAliasHeader:
    def test_prefixes_text_with_alias(self) -> None:
        out = _with_alias_header("file body", "turn0file1")
        assert out == "[inline alias: turn0file1]\nfile body"

    def test_none_alias_is_a_noop(self) -> None:
        assert _with_alias_header("file body", None) == "file body"
