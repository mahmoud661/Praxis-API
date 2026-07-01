from __future__ import annotations

import asyncio
from collections import defaultdict

from ..domain.ports.i_sandbox_client import CommandResult, ISandboxClient, SandboxInfo
from ..infrastructure.config.env import Env


class SandboxService:
    """Application-layer service for sandbox lifecycle management.

    Wraps an ISandboxClient and adds:
    - per-sandbox asyncio.Lock to serialise concurrent operations on the
      same sandbox (E2B SDK is not thread/concurrent-call-safe per handle).
    - default timeout sourced from Env so routes don't need to carry it.

    This class is intentionally free of HTTP/WebSocket concerns; it knows
    nothing about request/response shapes (those live in the route layer).
    """

    def __init__(self, client: ISandboxClient, env: Env) -> None:
        self._client = client
        self._default_timeout = env.default_sandbox_timeout
        # One asyncio.Lock per sandbox_id.  defaultdict creates it on
        # first access so there's no up-front registration step.
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lock(self, sandbox_id: str) -> asyncio.Lock:
        return self._locks[sandbox_id]

    def _release_lock(self, sandbox_id: str) -> None:
        """Remove the lock entry for a terminated sandbox to free memory."""
        self._locks.pop(sandbox_id, None)

    # ------------------------------------------------------------------
    # ISandboxService implementation
    # ------------------------------------------------------------------

    async def create_sandbox(
        self, timeout_secs: int | None = None, project_id: str | None = None
    ) -> SandboxInfo:
        secs = timeout_secs if timeout_secs is not None else self._default_timeout
        return await self._client.create(secs, project_id=project_id)

    async def resume_sandbox(self, sandbox_id: str) -> SandboxInfo:
        async with self._lock(sandbox_id):
            return await self._client.resume(sandbox_id)

    async def pause_sandbox(self, sandbox_id: str) -> None:
        async with self._lock(sandbox_id):
            await self._client.pause(sandbox_id)
        self._release_lock(sandbox_id)

    async def kill_sandbox(self, sandbox_id: str) -> None:
        async with self._lock(sandbox_id):
            await self._client.kill(sandbox_id)
        self._release_lock(sandbox_id)

    async def exec_command(self, sandbox_id: str, cmd: str) -> CommandResult:
        async with self._lock(sandbox_id):
            return await self._client.run_command(sandbox_id, cmd)

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        async with self._lock(sandbox_id):
            await self._client.write_file(sandbox_id, path, content)

    async def read_file(self, sandbox_id: str, path: str) -> str:
        async with self._lock(sandbox_id):
            return await self._client.read_file(sandbox_id, path)

    async def list_files(self, sandbox_id: str, path: str) -> list[str]:
        async with self._lock(sandbox_id):
            return await self._client.list_files(sandbox_id, path)

    async def get_stream_url(self, sandbox_id: str) -> str:
        # Read-only; still serialised so the handle lookup is consistent.
        async with self._lock(sandbox_id):
            return await self._client.get_stream_url(sandbox_id)

    async def internal_host(self, sandbox_id: str) -> str:
        # Reachable host for the preview reverse-proxy. Not lock-serialised
        # so proxied traffic doesn't contend with exec/file operations.
        return await self._client.internal_host(sandbox_id)

    async def open_terminal(self, sandbox_id: str, *, cols: int, rows: int):
        # Long-lived interactive shell. NOT lock-serialised — it must not
        # block exec/file ops for the session's whole lifetime.
        return await self._client.open_terminal(sandbox_id, cols=cols, rows=rows)
