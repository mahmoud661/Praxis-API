"""RunManager — per-user concurrent-run cap.

`start_run` raises `RunLimitExceededError` when accepting a turn would push
the caller past `max_concurrent_runs_per_user` active+queued turns across
ALL their threads. The check fires as the FIRST statement — before Redis
sadd, thread-repo touch, or asyncio.create_task — so a rejection has no
observable side effects.

Strategy: pre-seed `_handles` to simulate in-flight runs, then call
`start_run` and observe. Tests that exercise the "accepted" path patch
`asyncio.create_task` so no background task actually runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.services._errors import RunLimitExceededError
from app.application.services.agentic.run_manager import RunHandle, RunManager, _QueuedTurn


def _noop_create_task(coro: Any, **_kw: Any) -> MagicMock:
    """Patch target for asyncio.create_task: closes the coroutine (so the
    event loop doesn't warn about it) and returns a MagicMock as the task."""
    if hasattr(coro, "close"):
        coro.close()
    return MagicMock()


# ---- hand-rolled fakes -------------------------------------------------------
# Same style as the rest of the test suite: concrete stubs, no Magic* at the
# boundary that would hide missing methods.


class _Logger:
    def info(self, *a: Any, **kw: Any) -> None: ...
    def warning(self, *a: Any, **kw: Any) -> None: ...
    def error(self, *a: Any, **kw: Any) -> None: ...


class _Redis:
    def __init__(self) -> None:
        self.sadd = AsyncMock(return_value=1)
        self.srem = AsyncMock(return_value=1)
        self.publish = AsyncMock(return_value=0)
        self.smembers = AsyncMock(return_value=set())


class _Stream:
    def __init__(self) -> None:
        self.add = AsyncMock()
        self.replay = AsyncMock(return_value=[])
        self.read_blocking = AsyncMock(return_value=[])
        self.delete = AsyncMock()


class _ThreadRepo:
    def __init__(self) -> None:
        self.touch = AsyncMock()


class _Runner:
    async def run(self, **kw: Any) -> None:
        return


def make_manager(max_runs: int = 4) -> RunManager:
    return RunManager(
        runner=_Runner(),
        event_stream=_Stream(),
        redis=_Redis(),
        logger=_Logger(),
        thread_repo=_ThreadRepo(),
        max_concurrent_runs_per_user=max_runs,
    )


def _handle(thread_id: str, owner_id: str, queued: int = 0) -> RunHandle:
    """Create a RunHandle with `queued` pre-populated turns (no task)."""
    h = RunHandle(
        thread_id=thread_id,
        owner_id=owner_id,
        started_at=datetime.now(timezone.utc),
    )
    for i in range(queued):
        h.queue.append(
            _QueuedTurn(id=f"q{i}", content="pending", enqueued_at="2026-01-01T00:00:00Z")
        )
    return h


# ---- tests -------------------------------------------------------------------


async def test_first_run_is_accepted_and_registered():
    """An empty manager creates a handle and returns True (started immediately)."""
    manager = make_manager(max_runs=2)
    with patch("asyncio.create_task", side_effect=_noop_create_task):
        result = await manager.start_run(thread_id="t1", owner_id="u1", content="hello")

    assert result is True
    assert "t1" in manager._handles
    assert manager._handles["t1"].owner_id == "u1"


async def test_limit_not_raised_one_below_cap():
    """A user at cap−1 active turns can still start another run."""
    manager = make_manager(max_runs=3)
    # 1 active + 1 queued = 2 in-flight, cap is 3 → still room.
    manager._handles["t1"] = _handle("t1", "u1", queued=1)

    with patch("asyncio.create_task", side_effect=_noop_create_task):
        # t2 is a new thread with no existing handle → starts immediately.
        result = await manager.start_run(thread_id="t2", owner_id="u1", content="go")

    assert result is True


async def test_limit_raised_at_the_cap():
    """RunLimitExceededError fires when in-flight equals the cap exactly."""
    manager = make_manager(max_runs=2)
    # Two active handles → in_flight = 2 = cap.
    manager._handles["t1"] = _handle("t1", "u1")
    manager._handles["t2"] = _handle("t2", "u1")

    with pytest.raises(RunLimitExceededError) as exc:
        await manager.start_run(thread_id="t3", owner_id="u1", content="overflow")

    assert exc.value.limit == 2
    assert exc.value.owner_id == "u1"
    assert "u1" in str(exc.value)


async def test_queued_turns_count_toward_the_cap():
    """Queued turns (not just active handles) count toward the per-user cap."""
    manager = make_manager(max_runs=3)
    # 1 active + 2 queued = 3 in-flight = cap.
    manager._handles["t1"] = _handle("t1", "u1", queued=2)

    with pytest.raises(RunLimitExceededError) as exc:
        await manager.start_run(thread_id="t2", owner_id="u1", content="overflow")

    assert exc.value.limit == 3


async def test_users_are_counted_independently():
    """u1 at cap must not block u2; after u2 accepts, u1 is still blocked."""
    manager = make_manager(max_runs=1)
    # u1 is at cap (one active handle).
    manager._handles["t1"] = _handle("t1", "u1")

    # u2 starts fresh — must be accepted, no RunLimitExceededError.
    with patch("asyncio.create_task", side_effect=_noop_create_task):
        result = await manager.start_run(thread_id="t2", owner_id="u2", content="hi")

    assert result is True
    assert "t2" in manager._handles

    # u1 is still blocked.
    with pytest.raises(RunLimitExceededError):
        await manager.start_run(thread_id="t3", owner_id="u1", content="blocked")


async def test_no_side_effects_on_rejection():
    """When the cap is exceeded, Redis and the thread-repo are never touched."""
    manager = make_manager(max_runs=1)
    manager._handles["t1"] = _handle("t1", "u1")

    redis: _Redis = manager._redis
    repo: _ThreadRepo = manager._thread_repo

    with pytest.raises(RunLimitExceededError):
        await manager.start_run(thread_id="t2", owner_id="u1", content="rejected")

    # The check fires before any I/O — nothing should have been called.
    redis.sadd.assert_not_called()
    repo.touch.assert_not_called()


async def test_cap_enforced_per_user_not_globally():
    """The cap is per-user: a full house for u1 must not change the limit for u2."""
    manager = make_manager(max_runs=2)
    # u1 fills the cap: 2 active handles.
    manager._handles["t1"] = _handle("t1", "u1")
    manager._handles["t2"] = _handle("t2", "u1")
    # u2 has no handles.

    # u2 can start two runs before hitting its own cap.
    with patch("asyncio.create_task", side_effect=_noop_create_task):
        r1 = await manager.start_run(thread_id="t3", owner_id="u2", content="first")
        r2 = await manager.start_run(thread_id="t4", owner_id="u2", content="second")

    assert r1 is True
    assert r2 is True

    # u2 now hits its own cap.
    with pytest.raises(RunLimitExceededError) as exc:
        await manager.start_run(thread_id="t5", owner_id="u2", content="over")

    assert exc.value.owner_id == "u2"
    assert exc.value.limit == 2
