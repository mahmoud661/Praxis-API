from __future__ import annotations

from typing import Literal

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
    project_id: str | None = Field(
        default=None,
        description=(
            "Owning project id. When set (local provider), the sandbox mounts "
            "the project's persistent volume at /workspace so files survive "
            "sandbox restarts. Omitted → an ephemeral scratch workspace."
        ),
        max_length=64,
    )
    template: str | None = Field(
        default=None,
        description=(
            "Starter template id (see GET /sandbox/templates). Applied only "
            "when the workspace is empty — an existing project volume is "
            "never overwritten."
        ),
        max_length=64,
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


class FileNode(BaseModel):
    """One node in the workspace file tree. Folders carry `children`
    (possibly empty); files have `children = None`. `id` is the path
    relative to the tree root — unique, and what the UI keys/expands on."""

    id: str
    name: str
    type: Literal["file", "folder"]
    children: list["FileNode"] | None = None


class FileTreeResponse(BaseModel):
    tree: list[FileNode]


FileNode.model_rebuild()


class StreamUrlResponse(BaseModel):
    url: str


class PortsResponse(BaseModel):
    """Ports the sandbox is currently listening on (0.0.0.0/::) — i.e. the
    ones reachable by the preview proxy."""

    ports: list[int]


class TemplateInfo(BaseModel):
    """A starter template the UI can offer at sandbox creation."""

    id: str
    name: str
    description: str
    start_command: str | None = None
    ports: list[int] = []


class TemplatesResponse(BaseModel):
    templates: list[TemplateInfo]


class MessageResponse(BaseModel):
    """Generic acknowledgement envelope."""
    message: str
