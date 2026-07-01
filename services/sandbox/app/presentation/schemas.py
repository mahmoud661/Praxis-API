from __future__ import annotations

from pydantic import BaseModel, Field


# ── Request bodies ────────────────────────────────────────────────────────────

class CreateSandboxRequest(BaseModel):
    """Optional overrides for sandbox creation."""
    timeout_secs: int | None = Field(
        default=None,
        description=(
            "Sandbox lifetime in seconds. "
            "Defaults to the service's configured default_sandbox_timeout."
        ),
        gt=0,
    )


class ExecCommandRequest(BaseModel):
    cmd: str = Field(..., description="Shell command to run inside the sandbox.")


class WriteFileRequest(BaseModel):
    path: str = Field(..., description="Absolute path in the sandbox filesystem.")
    content: str = Field(..., description="UTF-8 text content to write.")


# ── Response bodies ───────────────────────────────────────────────────────────

class SandboxResponse(BaseModel):
    sandbox_id: str
    stream_url: str


class CommandResultResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


class ReadFileResponse(BaseModel):
    content: str


class ListFilesResponse(BaseModel):
    files: list[str]


class StreamUrlResponse(BaseModel):
    url: str


class MessageResponse(BaseModel):
    """Generic acknowledgement envelope."""
    message: str
