"""WsConnectionRegistry — per-user WS connection slot accounting.

The registry backs the agents-WS connection cap: `try_acquire` before
accept, `release` in a `finally` on every disconnect path. These tests
pin the accounting rules: cap boundary, per-user independence, release
under exceptions, and double-release clamping.
"""

from __future__ import annotations

import pytest

from app.presentation.http.ws_connection_registry import WsConnectionRegistry


def test_acquire_succeeds_up_to_the_cap():
    registry = WsConnectionRegistry(max_per_user=3)
    assert registry.try_acquire("u1") is True
    assert registry.try_acquire("u1") is True
    assert registry.try_acquire("u1") is True
    assert registry.count("u1") == 3


def test_acquire_over_the_cap_is_rejected_and_not_counted():
    registry = WsConnectionRegistry(max_per_user=2)
    assert registry.try_acquire("u1")
    assert registry.try_acquire("u1")
    assert registry.try_acquire("u1") is False
    # The rejected attempt must NOT bump the count — otherwise a burst
    # of rejected dials would lock the user out forever.
    assert registry.count("u1") == 2


def test_release_frees_a_slot():
    registry = WsConnectionRegistry(max_per_user=1)
    assert registry.try_acquire("u1")
    assert registry.try_acquire("u1") is False
    registry.release("u1")
    assert registry.count("u1") == 0
    assert registry.try_acquire("u1") is True


def test_users_are_counted_independently():
    registry = WsConnectionRegistry(max_per_user=1)
    assert registry.try_acquire("u1")
    # u1 at cap must not affect u2.
    assert registry.try_acquire("u2") is True
    assert registry.try_acquire("u1") is False
    registry.release("u1")
    assert registry.count("u1") == 0
    assert registry.count("u2") == 1


def test_release_runs_even_when_the_guarded_body_raises():
    """Mirrors the route's acquire → try → finally-release shape."""
    registry = WsConnectionRegistry(max_per_user=1)

    def connection_that_crashes() -> None:
        assert registry.try_acquire("u1")
        try:
            raise RuntimeError("boom mid-session")
        finally:
            registry.release("u1")

    with pytest.raises(RuntimeError):
        connection_that_crashes()
    assert registry.count("u1") == 0
    assert registry.try_acquire("u1") is True


def test_release_without_acquire_is_a_clamped_no_op():
    registry = WsConnectionRegistry(max_per_user=2)
    registry.release("ghost")
    assert registry.count("ghost") == 0
    # A double-release bug must not let the count go negative and grant
    # extra headroom beyond the cap.
    assert registry.try_acquire("ghost")
    registry.release("ghost")
    registry.release("ghost")
    assert registry.count("ghost") == 0
    assert registry.try_acquire("ghost")
    assert registry.try_acquire("ghost")
    assert registry.try_acquire("ghost") is False
