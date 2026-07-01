from __future__ import annotations

import asyncio
import logging

import websockets
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from ...application.sandbox_service import SandboxService
from ..schemas import (
    CommandResultResponse,
    CreateSandboxRequest,
    ExecCommandRequest,
    ListFilesResponse,
    MessageResponse,
    ReadFileResponse,
    SandboxResponse,
    StreamUrlResponse,
    WriteFileRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sandbox"])


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
        """Provision a new E2B Desktop sandbox."""
        try:
            info = await service.create_sandbox(timeout_secs=body.timeout_secs)
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
        await websocket.accept()

        try:
            stream_url = await service.get_stream_url(sandbox_id)
        except ValueError:
            await websocket.close(code=4004, reason="Sandbox not found.")
            return
        except Exception:
            logger.exception("sandbox.stream.get_url_failed", extra={"sandbox_id": sandbox_id})
            await websocket.close(code=4500, reason="Internal error.")
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

    return router


__all__ = ["_make_router"]
