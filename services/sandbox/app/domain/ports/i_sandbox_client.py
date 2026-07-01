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


class PtySession(Protocol):
    """A live interactive shell (PTY) inside a sandbox. Bytes flow both
    ways; the route relays them to/from a WebSocket. Tty is enabled, so the
    stream is raw (not multiplexed)."""

    async def read(self) -> bytes:
        """Read a chunk of terminal output. Returns b"" at EOF (shell exit)."""
        ...

    async def write(self, data: bytes) -> None:
        """Write keystrokes to the shell's stdin."""
        ...

    async def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY so full-screen programs (vim, htop) render right."""
        ...

    async def close(self) -> None:
        """Tear down the shell + connection."""
        ...


class ISandboxClient(Protocol):
    """Port for the E2B Desktop sandbox driver.

    All methods are async; concrete adapters are responsible for
    wrapping synchronous SDK calls in run_in_executor.
    """

    async def open_terminal(
        self, sandbox_id: str, *, cols: int, rows: int
    ) -> "PtySession":
        """Open an interactive shell (PTY) in the sandbox. Providers without a
        PTY raise NotImplementedError."""
        ...

    async def create(
        self, timeout_secs: int, project_id: str | None = None
    ) -> SandboxInfo:
        """Provision a new sandbox and return its ID + VNC stream URL.

        `project_id`, when set, lets a provider attach persistent per-project
        storage (the local Docker driver mounts a named volume at /workspace);
        providers with their own persistence may ignore it."""
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

    async def internal_host(self, sandbox_id: str) -> str:
        """Return the host (IP or hostname) at which the sandbox's own
        published ports are reachable from this service — used by the
        preview reverse-proxy. Local Docker returns the container IP on the
        shared network; providers without a routable host raise
        NotImplementedError."""
        ...

    async def get_stream_url(self, sandbox_id: str) -> str:
        """Return the VNC stream target for the given sandbox.

        The route relays the browser's WebSocket to it. Two shapes:
          - `http(s)://…` (E2B) — proxied as a WebSocket.
          - `vnc://host:port` (local Docker) — relayed WS↔raw TCP to the
            container's x11vnc server."""
        ...

    async def shutdown(self) -> None:
        """Tear down all resources held by this client on service exit.

        E2B: kills all active cloud sandboxes (stops billing).
        Local Docker: no-op (containers are intentionally persistent)."""
        ...
