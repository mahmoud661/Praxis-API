"""Local Docker-backed sandbox driver — implements ISandboxClient.

A zero-credentials alternative to the E2B cloud provider: each sandbox is
a plain Docker container running on the host daemon, reached via the Engine
API over the mounted `/var/run/docker.sock`. Drop-in behind the same port,
selected with `SANDBOX_PROVIDER=local`.

Talks to the Engine API with httpx over a unix-socket transport — no extra
dependency, no docker CLI in the image.

Desktop stream: the sandbox image runs a headless X display (:99) plus an
`x11vnc` server on TCP 5900. `get_stream_url` returns `vnc://<ip>:5900`
(the container's IP on this service's Docker network); the route relays the
browser's WebSocket straight to that TCP port, and noVNC renders it. To make
the container reachable, each sandbox is attached to the SAME Docker network
this service runs on (discovered by self-inspection — no config needed).

Ownership note: the container id IS the sandbox id. The daemon is the
single source of truth, so a missing/removed container surfaces as a
`ValueError` (mapped to 404 by the route), matching the E2B adapter.

SECURITY: mounting the docker socket grants root-equivalent access to the
host. This provider is intended for LOCAL DEVELOPMENT only — do not enable
it on a shared/prod host.
"""
from __future__ import annotations

import asyncio
import io
import shlex
import socket
import struct
import tarfile
from posixpath import basename, dirname

import httpx

from ...domain.ports.i_sandbox_client import CommandResult, ISandboxClient, SandboxInfo  # noqa: F401
from ...infrastructure.config.env import Env

# Label stamped on every sandbox container so they're identifiable /
# reap-able out of band (`docker ps --filter label=praxis.sandbox`).
_LABEL = "praxis.sandbox"

# VNC server port inside each sandbox container (x11vnc).
_VNC_PORT = 5900

# Named-volume prefix for persistent per-project workspaces.
_VOLUME_PREFIX = "praxis-proj-"


def _project_volume(project_id: str) -> str:
    """Deterministic Docker volume name for a project's /workspace. Sanitised
    to the charset Docker allows in volume names ([a-zA-Z0-9][a-zA-Z0-9_.-])."""
    safe = "".join(c if (c.isalnum() or c in "_.-") else "-" for c in project_id)
    return f"{_VOLUME_PREFIX}{safe}"


def _make_tar(name: str, data: bytes) -> bytes:
    """Wrap a single file in an uncompressed tar (the shape the Docker
    `PUT /containers/{id}/archive` endpoint expects)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _demux(data: bytes) -> tuple[str, str]:
    """Split Docker's multiplexed exec stream into (stdout, stderr).

    With Tty disabled, the daemon frames output as repeated
    [stream(1B), 0,0,0, size(4B big-endian)] headers followed by `size`
    payload bytes. stream==2 is stderr; everything else is stdout."""
    out = bytearray()
    err = bytearray()
    i, n = 0, len(data)
    while i + 8 <= n:
        stream_type = data[i]
        size = struct.unpack(">I", data[i + 4 : i + 8])[0]
        i += 8
        chunk = data[i : i + size]
        i += size
        if stream_type == 2:
            err += chunk
        else:
            out += chunk
    return out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


# Idempotent boot of the headless X + VNC stack inside a sandbox container:
# Xvfb on :99, wait for it, an openbox session (its autostart — baked into
# the image — sets the wallpaper and starts picom, tint2, conky and a
# terminal), then x11vnc exporting :99 on TCP 5900. pgrep guards make
# re-runs safe; one exec. HOME is set explicitly so openbox's children
# (sakura, rofi) find their configs under /root.
_DESKTOP_BOOT = (
    "pgrep -x Xvfb >/dev/null 2>&1 || "
    "(Xvfb :99 -screen 0 1280x720x24 -ac >/tmp/xvfb.log 2>&1 &); "
    "for _ in 1 2 3 4 5 6 7 8 9 10; do "
    "xdpyinfo -display :99 >/dev/null 2>&1 && break; sleep 0.3; done; "
    # SHELL is required by VTE terminals (sakura) to know what to spawn —
    # without it the terminal opens shell-less and drops every keystroke.
    "pgrep -x openbox >/dev/null 2>&1 || "
    "(DISPLAY=:99 HOME=/root SHELL=/bin/bash "
    "openbox-session >/tmp/openbox.log 2>&1 &); "
    # -xkb: use the XKEYBOARD extension for a complete keysym→keycode map.
    # Without it x11vnc's core mapping drops keys (symbols, shifted chars),
    # so the keyboard only "half works" in the browser.
    "pgrep -x x11vnc >/dev/null 2>&1 || "
    "(x11vnc -display :99 -forever -shared -nopw -rfbport 5900 "
    "-xkb -quiet -bg -noxdamage >/tmp/x11vnc.log 2>&1); "
    "sleep 0.4"
)

# Start the sandbox's OWN Docker daemon (nested). Only works under a
# Docker-capable runtime (Sysbox); under plain runc dockerd can't start.
# Idempotent; waits until the daemon answers so the first `docker` command
# doesn't race the startup.
_DOCKER_BOOT = (
    "pgrep -x dockerd >/dev/null 2>&1 || "
    "(dockerd >/var/log/dockerd.log 2>&1 &); "
    "for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done"
)


# Boots the interactive shell with a real prompt. bash by default (falls back
# to sh); a generated rcfile sources the system/user rc, drops into
# /workspace, and sets a coloured PS1 that shows the current path — teal cwd
# + a green ❯ — so the terminal reads like a proper shell, not a blank void.
_SHELL_BOOT = "\n".join(
    [
        "cat >/tmp/praxis.rc <<'RC'",
        "[ -f /etc/bash.bashrc ] && . /etc/bash.bashrc",
        '[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"',
        "cd /workspace 2>/dev/null",
        r"export PS1='\[\e[38;5;44m\]\w\[\e[0m\] \[\e[38;5;42m\]❯\[\e[0m\] '",
        "export TERM=xterm-256color",
        "RC",
        # NB: don't redirect the shell's stderr — bash writes its PROMPT there.
        "command -v bash >/dev/null 2>&1 && exec bash --rcfile /tmp/praxis.rc -i || exec sh",
    ]
)


class LocalPtySession:
    """A live interactive shell over a *hijacked* Docker exec stream.

    A normal Engine-API request won't do: an interactive PTY needs the raw,
    bidirectional connection the daemon upgrades to on `/exec/{id}/start`.
    So we speak HTTP/1.1 directly over the docker socket, read past the
    response head, and hand back the raw reader/writer. Tty is on, so the
    stream is *not* multiplexed — bytes flow straight through both ways."""

    def __init__(
        self,
        exec_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        http: httpx.AsyncClient,
    ) -> None:
        self._exec_id = exec_id
        self._reader = reader
        self._writer = writer
        self._http = http

    async def read(self) -> bytes:
        return await self._reader.read(65536)

    async def write(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def resize(self, cols: int, rows: int) -> None:
        # Best-effort — a failed resize must not tear down the shell.
        try:
            await self._http.post(
                f"/exec/{self._exec_id}/resize", params={"h": rows, "w": cols}
            )
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


class LocalDockerSandboxClient:
    """Implements ISandboxClient against the local Docker Engine API."""

    def __init__(self, env: Env) -> None:
        self._image = env.local_sandbox_image
        # "" → Docker default runtime (runc); "sysbox-runc" → Sysbox
        # (unprivileged nested Docker). Applied to every sandbox on create.
        self._runtime = env.sandbox_runtime
        # Per-sandbox resource caps (0 = uncapped).
        self._memory_mb = env.sandbox_memory_mb
        self._cpus = env.sandbox_cpus
        self._pids_limit = env.sandbox_pids_limit
        # Raw docker.sock path — used for the interactive-shell hijack, which
        # httpx's normal request/response cycle can't do.
        self._socket = env.docker_socket
        self._http = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=env.docker_socket),
            base_url="http://docker",
            timeout=httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=5.0),
        )
        # sandbox_ids whose X + VNC stack has been booted this process
        # lifetime — so we only pay the boot cost once.
        self._desktop_ready: set[str] = set()
        # sandbox_ids whose nested dockerd has been started this process
        # lifetime.
        self._docker_ready: set[str] = set()
        # Docker network this service runs on; new sandboxes join it so we
        # can reach their VNC port. Resolved once by self-inspection.
        self._network: str | None = None
        self._network_resolved = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _not_found(sandbox_id: str) -> ValueError:
        return ValueError(
            f"No sandbox {sandbox_id!r} (the container may have been "
            f"removed, or was never created)."
        )

    async def _own_network(self) -> str | None:
        """The Docker network THIS service container is attached to. New
        sandboxes join it so their VNC port is reachable from here. Cached;
        best-effort (falls back to None → default bridge)."""
        if self._network_resolved:
            return self._network
        self._network_resolved = True
        try:
            r = await self._http.get(f"/containers/{socket.gethostname()}/json")
            if r.status_code == 200:
                nets = r.json().get("NetworkSettings", {}).get("Networks", {})
                self._network = next(iter(nets), None)
        except Exception:  # noqa: BLE001
            self._network = None
        return self._network

    async def _ensure_image(self) -> None:
        """Pull the base image if the daemon doesn't already have it."""
        r = await self._http.get(f"/images/{self._image}/json")
        if r.status_code == 200:
            return
        name = self._image
        if ":" in name.rsplit("/", 1)[-1]:
            repo, tag = name.rsplit(":", 1)
        else:
            repo, tag = name, "latest"
        async with self._http.stream(
            "POST",
            "/images/create",
            params={"fromImage": repo, "tag": tag},
            timeout=httpx.Timeout(600.0),
        ) as resp:
            resp.raise_for_status()
            async for _ in resp.aiter_lines():
                pass  # drain the pull-progress JSON stream to completion

    async def _exec(self, sandbox_id: str, cmd: str) -> CommandResult:
        create = await self._http.post(
            f"/containers/{sandbox_id}/exec",
            json={
                "AttachStdout": True,
                "AttachStderr": True,
                "Cmd": ["/bin/sh", "-c", cmd],
            },
        )
        if create.status_code == 404:
            raise self._not_found(sandbox_id)
        create.raise_for_status()
        exec_id = create.json()["Id"]

        start = await self._http.post(
            f"/exec/{exec_id}/start",
            json={"Detach": False, "Tty": False},
        )
        start.raise_for_status()
        stdout, stderr = _demux(start.content)

        info = await self._http.get(f"/exec/{exec_id}/json")
        info.raise_for_status()
        exit_code = int(info.json().get("ExitCode") or 0)
        return CommandResult(stdout=stdout, stderr=stderr, exit_code=exit_code)

    async def _ensure_desktop(self, sandbox_id: str) -> None:
        """Boot the X + VNC stack once per sandbox (idempotent container-side
        too, via pgrep guards)."""
        if sandbox_id in self._desktop_ready:
            return
        await self._exec(sandbox_id, _DESKTOP_BOOT)
        self._desktop_ready.add(sandbox_id)

    async def _ensure_docker(self, sandbox_id: str) -> None:
        """Start the sandbox's nested Docker daemon once, so `docker` /
        `docker compose` work inside it. Only meaningful under a
        Docker-capable runtime (Sysbox)."""
        if sandbox_id in self._docker_ready:
            return
        await self._exec(sandbox_id, _DOCKER_BOOT)
        self._docker_ready.add(sandbox_id)

    async def _container_ip(self, sandbox_id: str) -> str:
        """The sandbox container's IP on this service's network."""
        ins = await self._http.get(f"/containers/{sandbox_id}/json")
        if ins.status_code == 404:
            raise self._not_found(sandbox_id)
        ins.raise_for_status()
        nets = ins.json().get("NetworkSettings", {}).get("Networks", {})
        network = await self._own_network()
        # Prefer the shared network; fall back to whatever endpoint exists.
        endpoint = nets.get(network) if network else None
        if endpoint is None and nets:
            endpoint = next(iter(nets.values()))
        ip = (endpoint or {}).get("IPAddress") or ""
        if not ip:
            raise RuntimeError(f"sandbox {sandbox_id} has no reachable IP")
        return ip

    # ------------------------------------------------------------------
    # ISandboxClient implementation
    # ------------------------------------------------------------------

    async def _find_project_container(self, project_id: str) -> str | None:
        """Return the id of a RUNNING sandbox container for this project, if
        one exists (matched by the `praxis.project` label). Lets create be
        get-or-create so the agent and the UI share ONE sandbox per project."""
        import json as _json

        filters = _json.dumps(
            {"label": [f"praxis.project={project_id}"], "status": ["running"]}
        )
        r = await self._http.get("/containers/json", params={"filters": filters})
        if r.status_code != 200:
            return None
        items = r.json()
        return items[0]["Id"] if items else None

    async def create(
        self, timeout_secs: int, project_id: str | None = None
    ) -> SandboxInfo:
        # timeout_secs is an E2B billing concept (auto-kill after N secs).
        # Local containers have no per-second cost, so we keep them alive
        # until explicitly paused/killed and ignore the value.
        if project_id:
            # Get-or-create: reuse the project's running sandbox if there is
            # one (both the UI's "Start Sandbox" and the agent's tools route
            # through here, so they converge on the same container). Warm
            # path — nothing was (re)started.
            existing = await self._find_project_container(project_id)
            if existing:
                return SandboxInfo(
                    sandbox_id=existing, stream_url="", cold_start=False
                )
        await self._ensure_image()
        network = await self._own_network()
        host_config: dict = {}
        if network:
            # Join this service's network so we can reach the VNC port.
            host_config["NetworkMode"] = network
        if self._runtime:
            # e.g. "sysbox-runc" → unprivileged, Docker-capable sandbox.
            host_config["Runtime"] = self._runtime
        # Resource caps so one sandbox can't starve the host (or its
        # siblings). Engine API units: Memory in bytes, NanoCpus in 1e-9 CPU.
        if self._memory_mb > 0:
            host_config["Memory"] = self._memory_mb * 1024 * 1024
            # Same value for memory+swap = no swap escape hatch.
            host_config["MemorySwap"] = self._memory_mb * 1024 * 1024
        if self._cpus > 0:
            host_config["NanoCpus"] = int(self._cpus * 1_000_000_000)
        if self._pids_limit > 0:
            host_config["PidsLimit"] = self._pids_limit
        labels = {_LABEL: "true"}
        if project_id:
            # Persistent per-project storage: mount a named volume at
            # /workspace. Docker auto-creates the volume on first use and
            # does NOT remove it on `docker rm -v` (that only reaps ANONYMOUS
            # volumes), so files + project-local deps survive sandbox
            # kill/recreate. The volume name is derived deterministically
            # from the project id, so a fresh sandbox for the same project
            # reattaches the same files.
            volume = _project_volume(project_id)
            host_config["Binds"] = [f"{volume}:/workspace"]
            labels["praxis.project"] = project_id
        r = await self._http.post(
            "/containers/create",
            json={
                "Image": self._image,
                "Cmd": ["sleep", "infinity"],
                "Tty": False,
                "WorkingDir": "/workspace",
                "Labels": labels,
                "HostConfig": host_config,
            },
        )
        r.raise_for_status()
        sandbox_id = r.json()["Id"]

        start = await self._http.post(f"/containers/{sandbox_id}/start")
        start.raise_for_status()
        await self._exec(sandbox_id, "mkdir -p /workspace")
        # Under a Docker-capable runtime (Sysbox), bring up the sandbox's own
        # dockerd so `docker` / `docker compose` are ready to run systems.
        if self._runtime:
            try:
                await self._ensure_docker(sandbox_id)
            except Exception:  # noqa: BLE001
                # Non-fatal: the sandbox still works for files/commands even
                # if the nested daemon didn't come up.
                pass
        return SandboxInfo(sandbox_id=sandbox_id, stream_url="", cold_start=True)

    async def resume(self, sandbox_id: str) -> SandboxInfo:
        ins = await self._http.get(f"/containers/{sandbox_id}/json")
        if ins.status_code == 404:
            raise self._not_found(sandbox_id)
        ins.raise_for_status()
        state = ins.json().get("State", {})
        cold = False
        if state.get("Paused"):
            # Unpause: processes were frozen, not killed — warm.
            r = await self._http.post(f"/containers/{sandbox_id}/unpause")
            r.raise_for_status()
        elif not state.get("Running"):
            # Cold start of a stopped container: processes are gone but the
            # /workspace volume persists.
            r = await self._http.post(f"/containers/{sandbox_id}/start")
            r.raise_for_status()
            cold = True
        return SandboxInfo(sandbox_id=sandbox_id, stream_url="", cold_start=cold)

    async def pause(self, sandbox_id: str) -> None:
        r = await self._http.post(f"/containers/{sandbox_id}/pause")
        if r.status_code == 404:
            raise self._not_found(sandbox_id)
        r.raise_for_status()

    async def kill(self, sandbox_id: str) -> None:
        self._desktop_ready.discard(sandbox_id)
        self._docker_ready.discard(sandbox_id)
        r = await self._http.delete(
            f"/containers/{sandbox_id}",
            params={"force": "true", "v": "true"},
        )
        if r.status_code == 404:
            raise self._not_found(sandbox_id)
        r.raise_for_status()

    async def run_command(self, sandbox_id: str, cmd: str) -> CommandResult:
        return await self._exec(sandbox_id, cmd)

    async def run_detached(self, sandbox_id: str, cmd: str) -> None:
        """Fire-and-forget exec — starts the command and returns immediately.
        For boot-time work that must not block a create/resume response."""
        create = await self._http.post(
            f"/containers/{sandbox_id}/exec",
            json={"AttachStdout": False, "AttachStderr": False,
                  "Cmd": ["/bin/sh", "-c", cmd]},
        )
        if create.status_code == 404:
            raise self._not_found(sandbox_id)
        create.raise_for_status()
        start = await self._http.post(
            f"/exec/{create.json()['Id']}/start",
            json={"Detach": True, "Tty": False},
        )
        start.raise_for_status()

    async def write_file(self, sandbox_id: str, path: str, content: str) -> None:
        directory = dirname(path) or "/"
        name = basename(path)
        if not name:
            raise ValueError(f"{path!r} is not a file path.")
        mk = await self._exec(sandbox_id, f"mkdir -p {shlex.quote(directory)}")
        if mk.exit_code != 0:
            raise RuntimeError(mk.stderr or f"cannot create {directory}")
        tar = _make_tar(name, content.encode("utf-8"))
        r = await self._http.put(
            f"/containers/{sandbox_id}/archive",
            params={"path": directory},
            content=tar,
            headers={"Content-Type": "application/x-tar"},
        )
        if r.status_code == 404:
            raise self._not_found(sandbox_id)
        r.raise_for_status()

    async def read_file(self, sandbox_id: str, path: str) -> str:
        result = await self._exec(sandbox_id, f"cat {shlex.quote(path)}")
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or f"cannot read {path}")
        return result.stdout

    async def list_files(self, sandbox_id: str, path: str) -> list[str]:
        result = await self._exec(sandbox_id, f"ls -1A {shlex.quote(path)}")
        if result.exit_code != 0:
            raise RuntimeError(result.stderr.strip() or f"cannot list {path}")
        return [line for line in result.stdout.splitlines() if line]

    async def get_stream_url(self, sandbox_id: str) -> str:
        # Boot the X + VNC stack (idempotent), then hand the route a
        # `vnc://<ip>:<port>` target it relays the browser WebSocket to.
        await self._ensure_desktop(sandbox_id)
        ip = await self._container_ip(sandbox_id)
        return f"vnc://{ip}:{_VNC_PORT}"

    async def internal_host(self, sandbox_id: str) -> str:
        # The container's IP on this service's network — apps that bind
        # 0.0.0.0:<port> inside the sandbox are reachable here at <ip>:<port>.
        return await self._container_ip(sandbox_id)

    async def open_terminal(
        self, sandbox_id: str, *, cols: int = 80, rows: int = 24
    ) -> LocalPtySession:
        # Interactive login shell in the workspace. bash with a real TERM so
        # colours + line editing work; falls back to sh if bash is absent.
        create = await self._http.post(
            f"/containers/{sandbox_id}/exec",
            json={
                "AttachStdin": True,
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": True,
                "Cmd": ["/bin/sh", "-c", _SHELL_BOOT],
                "WorkingDir": "/workspace",
                "Env": ["TERM=xterm-256color"],
            },
        )
        if create.status_code == 404:
            raise self._not_found(sandbox_id)
        create.raise_for_status()
        exec_id = create.json()["Id"]

        # Hijack: open the socket ourselves and request the connection upgrade
        # so we get the raw duplex stream instead of a buffered response.
        reader, writer = await asyncio.open_unix_connection(self._socket)
        body = b'{"Detach":false,"Tty":true}'
        request = (
            f"POST /exec/{exec_id}/start HTTP/1.1\r\n"
            "Host: docker\r\n"
            "Content-Type: application/json\r\n"
            "Connection: Upgrade\r\n"
            "Upgrade: tcp\r\n"
            f"Content-Length: {len(body)}\r\n"
            "\r\n"
        ).encode() + body
        writer.write(request)
        await writer.drain()

        # Drain the HTTP response head; everything after the blank line is
        # the raw PTY stream.
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        session = LocalPtySession(exec_id, reader, writer, self._http)
        await session.resize(cols, rows)
        return session


    async def shutdown(self) -> None:
        """Local sandbox containers are intentionally persistent — they survive
        service restarts and reattach the same project volume on next create.
        Killing them on shutdown would destroy in-progress work, so this is a
        deliberate no-op. The httpx client is closed so the socket fd is freed."""
        await self._http.aclose()


# Structural Protocol check at import time (no runtime overhead).
_: ISandboxClient = LocalDockerSandboxClient.__new__(LocalDockerSandboxClient)  # type: ignore[assignment]
