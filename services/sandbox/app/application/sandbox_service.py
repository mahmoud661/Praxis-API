from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import json

from ..domain import templates, workspace
from ..domain.ports.i_sandbox_client import CommandResult, ISandboxClient, SandboxInfo
from ..infrastructure.config.env import Env

logger = logging.getLogger(__name__)


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

    async def _bootstrap_project(self, sandbox_id: str) -> None:
        """Run the project's declared setup (`.praxis`) in the background so a
        freshly (re)started sandbox reinstalls its dependencies on its own.

        Application policy: fires only on cold starts (the adapter reports
        the fact via SandboxInfo.cold_start), is provider-agnostic, and is
        best-effort — a setup failure must never fail the start itself."""
        try:
            await self._client.run_detached(sandbox_id, workspace.PROJECT_SETUP_CMD)
        except Exception:  # noqa: BLE001
            logger.warning(
                "sandbox.bootstrap.failed", extra={"sandbox_id": sandbox_id}
            )

    async def _apply_template(self, sandbox_id: str, template_id: str) -> None:
        """Materialise a starter template into an EMPTY workspace: write its
        files + .praxis, then run its scaffold + setup in the background.
        A non-empty workspace (existing project volume) is left untouched —
        templates must never clobber user files."""
        template = templates.get_template(template_id)
        if template is None:
            logger.warning(
                "sandbox.template.unknown",
                extra={"sandbox_id": sandbox_id, "template": template_id},
            )
            return

        occupied = await self._client.run_command(
            sandbox_id, "ls -A /workspace | head -1"
        )
        if occupied.stdout.strip():
            logger.info(
                "sandbox.template.skipped_nonempty",
                extra={"sandbox_id": sandbox_id, "template": template_id},
            )
            return

        for path, content in template.files.items():
            await self._client.write_file(sandbox_id, path, content)
        await self._client.write_file(
            sandbox_id, "/workspace/.praxis", json.dumps(template.praxis, indent=2)
        )
        # Scaffold (if any) then dependency setup, detached — sandbox creation
        # must not wait on npm.
        chain = (
            f"{{ {template.scaffold_cmd} ; }} >>/tmp/praxis-scaffold.log 2>&1; "
            if template.scaffold_cmd
            else ""
        ) + workspace.PROJECT_SETUP_CMD
        await self._client.run_detached(sandbox_id, chain)

    async def create_sandbox(
        self,
        timeout_secs: int | None = None,
        project_id: str | None = None,
        template: str | None = None,
    ) -> SandboxInfo:
        secs = timeout_secs if timeout_secs is not None else self._default_timeout
        info = await self._client.create(secs, project_id=project_id)
        if not info.cold_start:
            return info
        if template:
            try:
                # Covers .praxis write + scaffold + setup in one chain.
                await self._apply_template(info.sandbox_id, template)
                return info
            except Exception:  # noqa: BLE001
                logger.warning(
                    "sandbox.template.failed",
                    extra={"sandbox_id": info.sandbox_id, "template": template},
                )
        if project_id:
            await self._bootstrap_project(info.sandbox_id)
        return info

    async def resume_sandbox(self, sandbox_id: str) -> SandboxInfo:
        async with self._lock(sandbox_id):
            info = await self._client.resume(sandbox_id)
        if info.cold_start:
            # PROJECT_SETUP_CMD no-ops when the workspace has no .praxis, so
            # firing it for non-project sandboxes is harmless.
            await self._bootstrap_project(sandbox_id)
        return info

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

    async def list_ports(self, sandbox_id: str) -> list[int]:
        """Reachable TCP ports the sandbox is listening on (Ports/Preview)."""
        result = await self.exec_command(sandbox_id, workspace.PROCNET_TCP_CMD)
        return workspace.parse_listening_ports(result.stdout)

    async def file_tree(self, sandbox_id: str, path: str) -> list[dict]:
        """The workspace as a nested folder/file tree (heavy dirs pruned,
        depth + entry count bounded)."""
        result = await self.exec_command(sandbox_id, workspace.tree_find_cmd(path))
        return workspace.build_file_tree(result.stdout)

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

    async def shutdown(self) -> None:
        """Propagate graceful-shutdown to the underlying client."""
        await self._client.shutdown()
