from __future__ import annotations

import asyncio
import json
import logging
import shlex

import httpx
import websockets
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from ...application.sandbox_service import SandboxService
from ..schemas import (
    CommandResultResponse,
    CreateSandboxRequest,
    ExecCommandRequest,
    FileTreeResponse,
    ListFilesResponse,
    MessageResponse,
    PortsResponse,
    ReadFileResponse,
    SandboxResponse,
    StreamUrlResponse,
    WriteFileRequest,
)

# Hop-by-hop headers not forwarded through the preview reverse-proxy.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def _parse_listening_ports(procnet: str) -> list[int]:
    """Parse `/proc/net/tcp{,6}` and return TCP ports in LISTEN state bound to
    all interfaces (0.0.0.0 / ::) — those the preview proxy can reach.
    Loopback-only binds are skipped (unreachable from this service)."""
    ports: set[int] = set()
    for line in procnet.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local, state = parts[1], parts[3]
        if state != "0A" or ":" not in local:  # 0A = TCP_LISTEN
            continue
        ip_hex, _, port_hex = local.rpartition(":")
        if set(ip_hex) != {"0"}:  # only all-interfaces binds are reachable
            continue
        try:
            ports.add(int(port_hex, 16))
        except ValueError:
            continue
    ports.discard(5900)  # x11vnc — internal, not a user app
    return sorted(ports)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sandbox"])


async def _relay_vnc_tcp(websocket: WebSocket, target: str) -> None:
    """Relay the browser WebSocket to a raw VNC TCP endpoint (local Docker
    provider). `target` is `vnc://host:port` — the sandbox container's
    x11vnc server. This is exactly what websockify does: browser noVNC
    speaks the RFB protocol as binary WS frames; we pipe those bytes to the
    VNC TCP socket and back. Bidirectional; first side to end cancels the
    other."""
    rest = target[len("vnc://") :]
    host, _, port_s = rest.partition(":")
    port = int(port_s or 5900)

    try:
        reader, writer = await asyncio.open_connection(host, port)
    except Exception:
        logger.exception("sandbox.stream.vnc_connect_failed", extra={"target": target})
        await websocket.close(code=4500, reason="VNC connect failed.")
        return

    async def ws_to_tcp() -> None:
        async for chunk in websocket.iter_bytes():
            writer.write(chunk)
            await writer.drain()

    async def tcp_to_ws() -> None:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await websocket.send_bytes(data)

    try:
        done, pending = await asyncio.wait(
            [asyncio.ensure_future(ws_to_tcp()), asyncio.ensure_future(tcp_to_ws())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("sandbox.stream.vnc_relay_error", extra={"target": target})
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


# Directories excluded from the file tree — huge / noise. They still show up
# as folder nodes; we just don't descend into them.
_TREE_PRUNE = (
    "node_modules", ".git", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".cache", ".mypy_cache", ".pytest_cache",
)
_TREE_MAXDEPTH = 8
_TREE_MAX_ENTRIES = 4000


def _tree_find_cmd(path: str) -> str:
    """A single `find` that lists the workspace as `<type>\\t<relpath>` lines,
    pruning heavy dirs and bounding depth + count. GNU find only (`-printf`)."""
    q = shlex.quote(path)
    names = " -o ".join(f"-name {shlex.quote(n)}" for n in _TREE_PRUNE)
    return (
        f"find {q} -mindepth 1 -maxdepth {_TREE_MAXDEPTH} "
        f"\\( {names} \\) -prune -printf '%y\\t%P\\n' "
        f"-o -printf '%y\\t%P\\n' 2>/dev/null | head -n {_TREE_MAX_ENTRIES}"
    )


def _build_tree(stdout: str) -> list[dict]:
    """Turn the flat `<type>\\t<relpath>` listing into a nested tree
    (folders-first, alphabetical). Shape matches the frontend Magic UI
    `TreeViewElement` (id/name/type/children)."""
    entries: list[tuple[str, str]] = []
    for line in stdout.splitlines():
        if "\t" not in line:
            continue
        typ, rel = line.split("\t", 1)
        if rel:
            entries.append((typ, rel))
    # Shallower paths first so a parent always exists before its children.
    entries.sort(key=lambda e: e[1].count("/"))

    nodes: dict[str, dict] = {}
    roots: list[dict] = []
    for typ, rel in entries:
        parts = rel.split("/")
        is_dir = typ == "d"
        node: dict = {
            "id": rel,
            "name": parts[-1],
            "type": "folder" if is_dir else "file",
            "children": [] if is_dir else None,
        }
        nodes[rel] = node
        parent = "/".join(parts[:-1])
        bucket = roots
        if parent:
            p = nodes.get(parent)
            if p is not None and p.get("children") is not None:
                bucket = p["children"]
        bucket.append(node)

    def _sort(level: list[dict]) -> None:
        level.sort(key=lambda n: (n["type"] != "folder", n["name"].lower()))
        for n in level:
            if n.get("children"):
                _sort(n["children"])

    _sort(roots)
    return roots


def _make_router(service: SandboxService) -> APIRouter:
    """Build and return the router with `service` closed over.

    Called once at boot by the DI container; the returned router is
    mounted on the FastAPI app.  Using a factory keeps the service
    instance off module-level state and avoids Depends() gymnastics
    for a service that has no per-request lifecycle.
    """

    # ── Create ────────────────────────────────────────────────────────────────

    @router.post("/sandbox", response_model=SandboxResponse, status_code=status.HTTP_201_CREATED)
    async def create_sandbox(body: CreateSandboxRequest = CreateSandboxRequest()) -> SandboxResponse:
        """Provision a new sandbox (mounts the project volume when project_id is set)."""
        try:
            info = await service.create_sandbox(
                timeout_secs=body.timeout_secs, project_id=body.project_id
            )
        except Exception as exc:
            logger.exception("sandbox.create.failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return SandboxResponse(sandbox_id=info.sandbox_id, stream_url=info.stream_url)

    # ── Resume ────────────────────────────────────────────────────────────────

    @router.post("/sandbox/{sandbox_id}/resume", response_model=SandboxResponse)
    async def resume_sandbox(sandbox_id: str) -> SandboxResponse:
        """Resume a previously paused sandbox."""
        try:
            info = await service.resume_sandbox(sandbox_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("sandbox.resume.failed", extra={"sandbox_id": sandbox_id})
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return SandboxResponse(sandbox_id=info.sandbox_id, stream_url=info.stream_url)

    # ── Pause ─────────────────────────────────────────────────────────────────

    @router.post("/sandbox/{sandbox_id}/pause", response_model=MessageResponse)
    async def pause_sandbox(sandbox_id: str) -> MessageResponse:
        """Checkpoint and suspend a running sandbox."""
        try:
            await service.pause_sandbox(sandbox_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("sandbox.pause.failed", extra={"sandbox_id": sandbox_id})
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return MessageResponse(message=f"Sandbox {sandbox_id} paused.")

    # ── Kill ──────────────────────────────────────────────────────────────────

    @router.delete("/sandbox/{sandbox_id}", response_model=MessageResponse)
    async def kill_sandbox(sandbox_id: str) -> MessageResponse:
        """Permanently destroy a sandbox."""
        try:
            await service.kill_sandbox(sandbox_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("sandbox.kill.failed", extra={"sandbox_id": sandbox_id})
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return MessageResponse(message=f"Sandbox {sandbox_id} destroyed.")

    # ── Exec ──────────────────────────────────────────────────────────────────

    @router.post("/sandbox/{sandbox_id}/exec", response_model=CommandResultResponse)
    async def exec_command(sandbox_id: str, body: ExecCommandRequest) -> CommandResultResponse:
        """Execute a shell command inside the sandbox."""
        try:
            result = await service.exec_command(sandbox_id, body.cmd)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("sandbox.exec.failed", extra={"sandbox_id": sandbox_id})
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return CommandResultResponse(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
        )

    # ── File write ────────────────────────────────────────────────────────────

    @router.post("/sandbox/{sandbox_id}/files/write", response_model=MessageResponse)
    async def write_file(sandbox_id: str, body: WriteFileRequest) -> MessageResponse:
        """Write a file into the sandbox filesystem."""
        try:
            await service.write_file(sandbox_id, body.path, body.content)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("sandbox.write_file.failed", extra={"sandbox_id": sandbox_id})
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return MessageResponse(message=f"Written to {body.path}.")

    # ── File read ─────────────────────────────────────────────────────────────

    @router.get("/sandbox/{sandbox_id}/files/read", response_model=ReadFileResponse)
    async def read_file(
        sandbox_id: str,
        path: str = Query(..., description="Absolute path inside the sandbox."),
    ) -> ReadFileResponse:
        """Read a file from the sandbox filesystem."""
        try:
            content = await service.read_file(sandbox_id, path)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("sandbox.read_file.failed", extra={"sandbox_id": sandbox_id})
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ReadFileResponse(content=content)

    # ── File list ─────────────────────────────────────────────────────────────

    @router.get("/sandbox/{sandbox_id}/files/list", response_model=ListFilesResponse)
    async def list_files(
        sandbox_id: str,
        path: str = Query("/", description="Directory path to list."),
    ) -> ListFilesResponse:
        """List files in a sandbox directory."""
        try:
            files = await service.list_files(sandbox_id, path)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("sandbox.list_files.failed", extra={"sandbox_id": sandbox_id})
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ListFilesResponse(files=files)

    # ── File tree ─────────────────────────────────────────────────────────────

    @router.get("/sandbox/{sandbox_id}/files/tree", response_model=FileTreeResponse)
    async def files_tree(
        sandbox_id: str,
        path: str = Query("/workspace", description="Root directory to walk."),
    ) -> FileTreeResponse:
        """Return the workspace as a nested folder/file tree (heavy dirs
        pruned, depth + count bounded). Backs the workspace Files tab."""
        try:
            result = await service.exec_command(sandbox_id, _tree_find_cmd(path))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("sandbox.files_tree.failed", extra={"sandbox_id": sandbox_id})
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return FileTreeResponse(tree=_build_tree(result.stdout))

    # ── Ports (run engine) ──────────────────────────────────────────────────────

    @router.get("/sandbox/{sandbox_id}/ports", response_model=PortsResponse)
    async def sandbox_ports(sandbox_id: str) -> PortsResponse:
        """List the ports the sandbox is currently listening on (reachable
        binds), so the UI can offer Preview/open. Reads /proc/net so it needs
        no extra tools in the image."""
        try:
            result = await service.exec_command(
                sandbox_id, "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null"
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("sandbox.ports.failed", extra={"sandbox_id": sandbox_id})
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return PortsResponse(ports=_parse_listening_ports(result.stdout))

    # ── Preview reverse-proxy ────────────────────────────────────────────────────

    @router.api_route(
        "/sandbox/{sandbox_id}/proxy/{port}/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def sandbox_proxy(
        sandbox_id: str, port: int, path: str, request: Request
    ) -> Response:
        """Reverse-proxy an app running inside the sandbox (bound 0.0.0.0:port)
        so the browser can reach it. Note: path-prefixed — apps that use
        relative asset paths work best; absolute-root paths may need the
        subdomain proxy (future)."""
        try:
            host = await service.internal_host(sandbox_id)
        except ValueError:
            return Response("Sandbox not found.", status_code=404)
        except NotImplementedError:
            return Response("Preview is not available for this provider.", status_code=501)
        except Exception:
            logger.exception("sandbox.proxy.host_failed", extra={"sandbox_id": sandbox_id})
            return Response("Internal error.", status_code=500)

        target = f"http://{host}:{port}/{path}"
        fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
        body = await request.body()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False) as client:
                upstream = await client.request(
                    request.method, target,
                    params=request.query_params, headers=fwd_headers, content=body,
                )
        except Exception as exc:  # noqa: BLE001
            return Response(f"Preview upstream error: {exc}", status_code=502)
        resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=resp_headers,
            media_type=upstream.headers.get("content-type"),
        )

    # ── Stream URL ────────────────────────────────────────────────────────────

    @router.get("/sandbox/{sandbox_id}/stream-url", response_model=StreamUrlResponse)
    async def get_stream_url(sandbox_id: str) -> StreamUrlResponse:
        """Return the live VNC stream URL for the sandbox."""
        try:
            url = await service.get_stream_url(sandbox_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("sandbox.stream_url.failed", extra={"sandbox_id": sandbox_id})
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return StreamUrlResponse(url=url)

    # ── WebSocket stream proxy ─────────────────────────────────────────────────

    @router.websocket("/sandbox/{sandbox_id}/stream")
    async def sandbox_stream(websocket: WebSocket, sandbox_id: str) -> None:
        """Proxy the E2B VNC WebSocket stream to the connecting client.

        Bidirectional relay:
        - E2B → client: raw binary/text frames from the VNC server.
        - client → E2B: keyboard/mouse input frames sent by the frontend.

        Both relay tasks run concurrently; the first to complete (because
        either side disconnected) cancels the other so no zombie coroutines
        are left behind.
        """
        # noVNC negotiates the "binary" subprotocol — echo it back when
        # offered so the RFB client is happy; otherwise accept plainly.
        offered = websocket.scope.get("subprotocols") or []
        subprotocol = "binary" if "binary" in offered else None
        await websocket.accept(subprotocol=subprotocol)

        try:
            stream_url = await service.get_stream_url(sandbox_id)
        except ValueError:
            await websocket.close(code=4004, reason="Sandbox not found.")
            return
        except Exception:
            logger.exception("sandbox.stream.get_url_failed", extra={"sandbox_id": sandbox_id})
            await websocket.close(code=4500, reason="Internal error.")
            return

        # Local provider: `vnc://host:port` → relay WS ↔ raw VNC TCP.
        if stream_url.startswith("vnc://"):
            await _relay_vnc_tcp(websocket, stream_url)
            return

        # E2B returns an HTTP(S) URL; convert to WS(S) for the websockets library.
        ws_url = stream_url.replace("https://", "wss://").replace("http://", "ws://")

        try:
            async with websockets.connect(ws_url) as e2b_ws:

                async def relay_to_client() -> None:
                    """Forward frames from E2B to the browser."""
                    async for msg in e2b_ws:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)

                async def relay_to_e2b() -> None:
                    """Forward frames from the browser to E2B."""
                    async for msg in websocket.iter_bytes():
                        await e2b_ws.send(msg)

                done, pending = await asyncio.wait(
                    [
                        asyncio.ensure_future(relay_to_client()),
                        asyncio.ensure_future(relay_to_e2b()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()

        except WebSocketDisconnect:
            # Client disconnected cleanly — nothing to do.
            pass
        except websockets.exceptions.ConnectionClosed:
            # E2B side closed first — pass through.
            pass
        except Exception:
            logger.exception("sandbox.stream.proxy_error", extra={"sandbox_id": sandbox_id})
        finally:
            # close() is idempotent; safe even if the client already disconnected.
            try:
                await websocket.close()
            except Exception:
                pass

    # ── Interactive terminal (PTY) ──────────────────────────────────────────────

    @router.websocket("/sandbox/{sandbox_id}/pty")
    async def sandbox_pty(websocket: WebSocket, sandbox_id: str) -> None:
        """Bridge a browser terminal (xterm.js) to a real interactive shell
        inside the sandbox.

        Wire protocol:
          - client → server BINARY frames: raw keystrokes → the PTY's stdin.
          - client → server TEXT frames: JSON control, currently only
            `{"resize": {"cols": N, "rows": M}}`.
          - server → client BINARY frames: raw PTY output.

        Two relay tasks run concurrently; whichever finishes first (a side
        disconnected or the shell exited) cancels the other."""
        await websocket.accept()

        # Initial size can be hinted via query params so the first paint fits.
        def _int(name: str, default: int) -> int:
            try:
                return max(1, int(websocket.query_params.get(name, default)))
            except (TypeError, ValueError):
                return default

        try:
            session = await service.open_terminal(
                sandbox_id, cols=_int("cols", 80), rows=_int("rows", 24)
            )
        except ValueError:
            await websocket.close(code=4004, reason="Sandbox not found.")
            return
        except NotImplementedError:
            await websocket.close(code=4501, reason="Terminal not supported.")
            return
        except Exception:
            logger.exception("sandbox.pty.open_failed", extra={"sandbox_id": sandbox_id})
            await websocket.close(code=4500, reason="Internal error.")
            return

        async def pty_to_client() -> None:
            while True:
                data = await session.read()
                if not data:  # shell exited / stream closed
                    break
                await websocket.send_bytes(data)

        async def client_to_pty() -> None:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is not None:
                    await session.write(data)
                    continue
                text = msg.get("text")
                if text:
                    try:
                        ctrl = json.loads(text)
                    except ValueError:
                        continue
                    size = ctrl.get("resize")
                    if isinstance(size, dict):
                        await session.resize(
                            int(size.get("cols", 80)), int(size.get("rows", 24))
                        )

        try:
            done, pending = await asyncio.wait(
                [
                    asyncio.ensure_future(pty_to_client()),
                    asyncio.ensure_future(client_to_pty()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("sandbox.pty.relay_error", extra={"sandbox_id": sandbox_id})
        finally:
            await session.close()
            try:
                await websocket.close()
            except Exception:
                pass

    return router


__all__ = ["_make_router"]
