from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SandboxInfo:
    sandbox_id: str
    stream_url: str


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int


class ISandboxClient(Protocol):
    """Port for the E2B Desktop sandbox driver.

    All methods are async; concrete adapters are responsible for
    wrapping synchronous SDK calls in run_in_executor.
    """

    async def create(self, timeout_secs: int) -> SandboxInfo:
        """Provision a new sandbox and return its ID + VNC stream URL."""
        ...

    async def resume(self, sandbox_id: str) -> SandboxInfo:
        """Resume a previously paused sandbox."""
        ...

    async def pause(self, sandbox_id: str) -> None:
        """Checkpoint and pause a running sandbox."""
        ...

    async def kill(self, sandbox_id: str) -> None:
        """Permanently destroy a sandbox."""
        ...

    async def run_command(self, sandbox_id: str, cmd: str) -> CommandResult:
        """Execute `cmd` inside the sandbox shell and return the result."""
        ...

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        """Write `content` to `path` in the sandbox filesystem."""
        ...

    async def read_file(self, sandbox_id: str, path: str) -> str:
        """Return the text content of `path` from the sandbox filesystem."""
        ...

    async def list_files(self, sandbox_id: str, path: str) -> list[str]:
        """Return a list of file/directory names under `path`."""
        ...

    async def get_stream_url(self, sandbox_id: str) -> str:
        """Return the HTTP(S) VNC stream URL for the given sandbox."""
        ...
