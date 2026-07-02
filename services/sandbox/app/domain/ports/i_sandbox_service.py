from __future__ import annotations

from typing import Protocol

from .i_sandbox_client import CommandResult, SandboxInfo


class ISandboxService(Protocol):
    """Application-layer port for sandbox operations.

    Mirrors ISandboxClient but sits one level above the infrastructure
    boundary — callers (routes/controllers) depend on this protocol,
    not on the concrete E2B adapter.
    """

    async def create_sandbox(self, timeout_secs: int) -> SandboxInfo:
        """Create a new sandbox and return its identity + stream URL."""
        ...

    async def resume_sandbox(self, sandbox_id: str) -> SandboxInfo:
        """Resume a paused sandbox."""
        ...

    async def pause_sandbox(self, sandbox_id: str) -> None:
        """Pause a running sandbox."""
        ...

    async def kill_sandbox(self, sandbox_id: str) -> None:
        """Permanently destroy a sandbox."""
        ...

    async def exec_command(self, sandbox_id: str, cmd: str) -> CommandResult:
        """Run a shell command inside the sandbox."""
        ...

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        """Write content to a file in the sandbox."""
        ...

    async def read_file(self, sandbox_id: str, path: str) -> str:
        """Read a file from the sandbox."""
        ...

    async def list_files(self, sandbox_id: str, path: str) -> list[str]:
        """List files at a path in the sandbox."""
        ...

    async def get_stream_url(self, sandbox_id: str) -> str:
        """Return the live VNC stream URL for the sandbox."""
        ...
