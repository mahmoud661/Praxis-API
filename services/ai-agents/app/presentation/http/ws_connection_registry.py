"""
WsConnectionRegistry — per-user WebSocket connection counter.

Backs the agents-WS abuse cap (`Env.max_ws_connections_per_user`): the
route calls `try_acquire` before accepting a socket and `release` in a
`finally` on every disconnect path, so the count can't leak.

CAVEAT: the counter is plain in-process memory. That is correct for
this service *today* because it runs as a single process (one uvicorn
worker), so the in-memory count IS the global count. If the service
ever scales to multiple workers/pods, move this to a shared store
(e.g. a Redis hash with per-connection TTL heartbeats).

No locking: all access happens on the event loop thread and each
method is synchronous, so operations are atomic with respect to the
loop.
"""

from __future__ import annotations


class WsConnectionRegistry:
    """Registered to the DI token `"WsConnectionRegistry"`."""

    def __init__(self, max_per_user: int) -> None:
        self._max_per_user = max_per_user
        self._counts: dict[str, int] = {}

    def try_acquire(self, user_id: str) -> bool:
        """Claim a connection slot. Returns False (and counts nothing)
        when the user is already at the cap."""
        current = self._counts.get(user_id, 0)
        if current >= self._max_per_user:
            return False
        self._counts[user_id] = current + 1
        return True

    def release(self, user_id: str) -> None:
        """Free a slot. Releasing below zero is clamped (and the key
        dropped) so a double-release bug can't corrupt the count into
        letting a user hold negative connections."""
        current = self._counts.get(user_id, 0)
        if current <= 1:
            self._counts.pop(user_id, None)
        else:
            self._counts[user_id] = current - 1

    def count(self, user_id: str) -> int:
        return self._counts.get(user_id, 0)
