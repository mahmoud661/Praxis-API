"""
REST controller for `/v1/threads`. Pure HTTP shape — all the work happens in
`IThreadsService`.

Response models are Pydantic so OpenAPI is accurate; conversion from the
dataclass DTOs is one-liner. Keeping the wire shape stable here means future
internal DTO refactors don't ripple to the frontend.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...application.services._errors import (
    InvalidThreadConfigError,
    ThreadNotFoundError,
)
from ...domain.dtos.thread_dto import ThreadConfigView, ThreadView
from ...domain.IServices.i_threads_service import IThreadsService
from ..http.dependencies import current_user_id


class ThreadConfigResponse(BaseModel):
    """Per-thread overrides — mirrors `ThreadConfigView`. Empty fields
    mean "use the agent / account defaults" on the resolver side."""

    agent_id: str | None = None
    tool_overrides: dict[str, bool] = Field(default_factory=dict)
    custom_system_prompt_id: str | None = None

    @classmethod
    def from_view(cls, c: ThreadConfigView) -> "ThreadConfigResponse":
        return cls(
            agent_id=c.agent_id,
            tool_overrides=dict(c.tool_overrides),
            custom_system_prompt_id=c.custom_system_prompt_id,
        )


class ThreadResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    config: ThreadConfigResponse = Field(default_factory=ThreadConfigResponse)

    @classmethod
    def from_view(cls, t: ThreadView) -> "ThreadResponse":
        return cls(
            id=t.id,
            title=t.title,
            created_at=t.created_at,
            updated_at=t.updated_at,
            config=ThreadConfigResponse.from_view(t.config),
        )


class UpdateThreadConfigBody(BaseModel):
    """PATCH body. All fields optional — only the keys present in the
    request payload are written; missing keys keep their current
    value. Pass `tool_overrides: {}` to clear all overrides explicitly;
    omitting the key leaves them untouched."""

    agent_id: str | None = Field(default=None)
    tool_overrides: dict[str, bool] | None = Field(default=None)
    custom_system_prompt_id: str | None = Field(default=None)


class ThreadListResponse(BaseModel):
    threads: list[ThreadResponse]


class CreateThreadBody(BaseModel):
    # Optional — defaults to "New conversation" service-side.
    title: str | None = Field(default=None, max_length=120)


class HistoryToolCallResponse(BaseModel):
    id: str
    name: str
    args: dict
    # `null` if the tool hasn't reported back yet (which only happens
    # mid-stream — the history endpoint reads a settled checkpoint so in
    # practice this is always populated, but the shape mirrors the
    # frontend's live-stream representation).
    result: str | None = None


class HistoryAttachmentResponse(BaseModel):
    """Wire shape for one persisted attachment on a user message.
    Frontend renders these as chips/thumbnails. `id` doubles as the
    handle for `GET /v1/files/{id}/content` if the frontend wants to
    fetch bytes for a thumbnail."""

    id: str
    filename: str
    mime_type: str
    size_bytes: int


class HistoryMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    tool_calls: list[HistoryToolCallResponse] = Field(default_factory=list)
    attachments: list[HistoryAttachmentResponse] = Field(default_factory=list)
    # Resolved content references stamped on assistant messages by
    # `ContentReferenceMiddleware`. Each entry has `kind` +
    # `matched_text` + `start_idx` + `end_idx` + a payload keyed to
    # the kind (`attachment` block or `items` list for citations).
    # Wire shape is `list[dict]` because the union of variants is
    # easier to evolve than a Pydantic discriminated union here.
    content_references: list[dict] = Field(default_factory=list)


class HistoryResponse(BaseModel):
    messages: list[HistoryMessageResponse]
    # Pagination metadata — populated only by the paginated endpoint, but
    # safe to include on the full-history response too (defaults make
    # it look "complete"). Lets the frontend share one wire shape.
    has_more: bool = False
    next_cursor: str | None = None


class ThreadsController:
    """Container resolves `service: IThreadsService` from token "IThreadsService"."""

    def __init__(self, service: IThreadsService) -> None:
        self._service = service

    async def list_threads(
        self, user_id: str = Depends(current_user_id)
    ) -> ThreadListResponse:
        threads = await self._service.list_for_owner(user_id)
        return ThreadListResponse(
            threads=[ThreadResponse.from_view(t) for t in threads]
        )

    async def create_thread(
        self,
        body: CreateThreadBody,
        user_id: str = Depends(current_user_id),
    ) -> ThreadResponse:
        thread = await self._service.create(owner_id=user_id, title=body.title)
        return ThreadResponse.from_view(thread)

    async def get_thread(
        self, thread_id: str, user_id: str = Depends(current_user_id)
    ) -> ThreadResponse:
        try:
            thread = await self._service.get(thread_id=thread_id, owner_id=user_id)
        except ThreadNotFoundError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
        return ThreadResponse.from_view(thread)

    async def delete_thread(
        self, thread_id: str, user_id: str = Depends(current_user_id)
    ):
        # No return-type annotation on purpose — FastAPI auto-builds a
        # response field from the annotation, which clashes with the 204
        # status code ("must not have a body").
        try:
            await self._service.delete(thread_id=thread_id, owner_id=user_id)
        except ThreadNotFoundError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})

    async def update_thread_config(
        self,
        thread_id: str,
        body: UpdateThreadConfigBody,
        user_id: str = Depends(current_user_id),
    ) -> ThreadResponse:
        """PATCH /v1/threads/{thread_id}/config.

        Reads the current config (404 if the thread is missing or not
        owned), merges in the body's set fields, and writes the
        validated result. Unset body fields keep their current value —
        sparse PATCH semantics. Pass `tool_overrides: {}` (empty
        object) to clear overrides explicitly.
        """
        try:
            current = await self._service.get(
                thread_id=thread_id, owner_id=user_id
            )
        except ThreadNotFoundError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})

        merged = ThreadConfigView(
            agent_id=(
                body.agent_id if "agent_id" in body.model_fields_set
                else current.config.agent_id
            ),
            tool_overrides=(
                dict(body.tool_overrides)
                if body.tool_overrides is not None
                else dict(current.config.tool_overrides)
            ),
            custom_system_prompt_id=(
                body.custom_system_prompt_id
                if "custom_system_prompt_id" in body.model_fields_set
                else current.config.custom_system_prompt_id
            ),
        )
        try:
            updated = await self._service.update_config(
                thread_id=thread_id, owner_id=user_id, config=merged
            )
        except ThreadNotFoundError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
        except InvalidThreadConfigError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "INVALID_CONFIG", "message": str(exc)},
            )
        return ThreadResponse.from_view(updated)

    async def thread_history(
        self,
        thread_id: str,
        user_id: str = Depends(current_user_id),
        limit: int | None = Query(
            default=None,
            ge=1,
            le=200,
            description=(
                "Page size. When set, the endpoint returns at most this "
                "many messages and includes pagination cursors."
            ),
        ),
        before: str | None = Query(
            default=None,
            description=(
                "Message id cursor. Returns the page that ends just "
                "before this message — i.e. the next-older page."
            ),
        ),
    ) -> HistoryResponse:
        try:
            if limit is None and before is None:
                # Back-compat: no pagination params → full history, all in
                # one response (matches the original wire shape).
                messages = await self._service.load_messages(
                    thread_id=thread_id, owner_id=user_id
                )
                page_has_more = False
                page_cursor: str | None = None
            else:
                page = await self._service.load_messages_page(
                    thread_id=thread_id,
                    owner_id=user_id,
                    limit=limit or 30,
                    before=before,
                )
                messages = list(page.messages)
                page_has_more = page.has_more
                page_cursor = page.next_cursor
        except ThreadNotFoundError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
        return HistoryResponse(
            messages=[
                HistoryMessageResponse(
                    id=m.id,
                    role=m.role,
                    content=m.content,
                    tool_calls=[
                        HistoryToolCallResponse(
                            id=tc.id,
                            name=tc.name,
                            args=tc.args,
                            result=tc.result,
                        )
                        for tc in m.tool_calls
                    ],
                    attachments=[
                        HistoryAttachmentResponse(
                            id=a.id,
                            filename=a.filename,
                            mime_type=a.mime_type,
                            size_bytes=a.size_bytes,
                        )
                        for a in m.attachments
                    ],
                    content_references=list(m.content_references),
                )
                for m in messages
            ],
            has_more=page_has_more,
            next_cursor=page_cursor,
        )
