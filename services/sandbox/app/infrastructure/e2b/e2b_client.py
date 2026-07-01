from __future__ import annotations

import asyncio
from functools import partial

from e2b_desktop import Sandbox

from ...domain.ports.i_sandbox_client import CommandResult, ISandboxClient, SandboxInfo
from ...infrastructure.config.env import Env


class E2BSandboxClient:
    """Concrete adapter that wraps the synchronous E2B Desktop SDK.

    The SDK is blocking (no async support), so every call is dispatched
    to the default ThreadPoolExecutor via ``loop.run_in_executor``.  This
    keeps the FastAPI event loop free while E2B operations are in flight.

    Sandbox objects are cached in ``_active_sandboxes`` for the duration
    of their lifetime.  Pausing or killing a sandbox removes it from the
    cache; resuming inserts a fresh handle.
    """

    def __init__(self, env: Env) -> None:
        self._api_key = env.e2b_api_key
        self._default_timeout = env.default_sandbox_timeout
        # sandbox_id -> live Sandbox handle
        self._active_sandboxes: dict[str, Sandbox] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, sandbox_id: str) -> Sandbox:
        try:
            return self._active_sandboxes[sandbox_id]
        except KeyError:
            raise ValueError(
                f"No active sandbox handle for {sandbox_id!r}. "
                "The sandbox may have been paused or killed, or was never created "
                "by this service instance."
            )

    async def _run(self, fn, *args, **kwargs):
        """Execute a sync callable in the thread-pool and await the result."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))

    # ------------------------------------------------------------------
    # ISandboxClient implementation
    # ------------------------------------------------------------------

    def _stream_url(self, sbx: Sandbox) -> str:
        """Start the VNC server (idempotent) and return its stream URL.

        `stream` is a property on the 2.x SDK; the VNC server must be
        started before `get_url()` works. `start()` raises if it's already
        running, so a second call (e.g. a later get_stream_url) is treated
        as a no-op."""
        try:
            sbx.stream.start()
        except Exception:  # noqa: BLE001 — already started; url still valid
            pass
        return sbx.stream.get_url()

    async def create(
        self, timeout_secs: int, project_id: str | None = None
    ) -> SandboxInfo:
        # e2b-desktop 2.x: `Sandbox.create(...)` is the factory (the old
        # `Sandbox(api_key=...)` constructor no longer accepts api_key).
        # `project_id` is ignored — E2B manages its own persistence.
        def _create() -> Sandbox:
            return Sandbox.create(timeout=timeout_secs, api_key=self._api_key)

        sbx: Sandbox = await self._run(_create)
        self._active_sandboxes[sbx.sandbox_id] = sbx
        stream_url: str = await self._run(self._stream_url, sbx)
        return SandboxInfo(sandbox_id=sbx.sandbox_id, stream_url=stream_url)

    async def resume(self, sandbox_id: str) -> SandboxInfo:
        # 2.x replaced `Sandbox.resume(id)` with `Sandbox.connect(id)`,
        # which reconnects to a running sandbox (auto-resuming a paused one).
        def _resume() -> Sandbox:
            return Sandbox.connect(sandbox_id, api_key=self._api_key)

        sbx: Sandbox = await self._run(_resume)
        self._active_sandboxes[sandbox_id] = sbx
        stream_url: str = await self._run(self._stream_url, sbx)
        return SandboxInfo(sandbox_id=sandbox_id, stream_url=stream_url)

    async def pause(self, sandbox_id: str) -> None:
        sbx = self._get(sandbox_id)
        await self._run(sbx.pause)
        self._active_sandboxes.pop(sandbox_id, None)

    async def kill(self, sandbox_id: str) -> None:
        sbx = self._get(sandbox_id)
        await self._run(sbx.kill)
        self._active_sandboxes.pop(sandbox_id, None)

    async def run_command(self, sandbox_id: str, cmd: str) -> CommandResult:
        sbx = self._get(sandbox_id)
        result = await self._run(sbx.commands.run, cmd)
        return CommandResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            exit_code=result.exit_code,
        )

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        sbx = self._get(sandbox_id)
        # 2.x: the filesystem API moved from `.filesystem` to `.files`.
        await self._run(sbx.files.write, path, content)

    async def read_file(self, sandbox_id: str, path: str) -> str:
        sbx = self._get(sandbox_id)
        return await self._run(sbx.files.read, path)

    async def list_files(self, sandbox_id: str, path: str) -> list[str]:
        sbx = self._get(sandbox_id)
        entries = await self._run(sbx.files.list, path)
        # EntryInfo objects expose a `.name` attribute; fall back to str()
        # if the SDK ever changes the return type.
        return [getattr(e, "name", str(e)) for e in entries]

    async def get_stream_url(self, sandbox_id: str) -> str:
        sbx = self._get(sandbox_id)
        return await self._run(self._stream_url, sbx)


# Satisfy the structural Protocol check at import time (no runtime overhead).
_: ISandboxClient = E2BSandboxClient.__new__(E2BSandboxClient)  # type: ignore[assignment]
