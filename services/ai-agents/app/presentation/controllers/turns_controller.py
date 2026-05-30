"""
REST controller for the `/v1/threads/{tid}/turns/*` endpoints — retry and
edit. The work itself happens in `TurnsService`; this layer just unpacks
the request, maps domain errors to HTTP, and returns an empty body. The
new run streams to the user over the existing WebSocket exactly as if
they'd typed and submitted a fresh message.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from ...application.services._errors import (
    InvalidTurnTargetError,
    MessageNotFoundError,
    ThreadNotFoundError,
    TurnInProgressError,
)
from ...application.services.turns_service import TurnsService
from ..http.dependencies import current_user_id


class RetryTurnBody(BaseModel):
    # The LangChain message id of the user message to retry from.
    message_id: str = Field(min_length=1)


class EditTurnBody(BaseModel):
    message_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=8000)


class TurnsController:
    """Container resolves `service: ITurnsService` from token "ITurnsService"."""

    def __init__(self, service: TurnsService) -> None:
        self._service = service

    async def retry(
        self,
        thread_id: str,
        body: RetryTurnBody,
        user_id: str = Depends(current_user_id),
    ):
        try:
            await self._service.retry(
                thread_id=thread_id,
                owner_id=user_id,
                message_id=body.message_id,
            )
        except ThreadNotFoundError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
        except MessageNotFoundError:
            raise HTTPException(
                status_code=404,
                detail={"error": "MESSAGE_NOT_FOUND"},
            )
        except InvalidTurnTargetError as err:
            raise HTTPException(
                status_code=400,
                detail={"error": "INVALID_TARGET", "message": str(err)},
            )
        except TurnInProgressError:
            raise HTTPException(
                status_code=409,
                detail={"error": "TURN_IN_PROGRESS"},
            )

    async def edit(
        self,
        thread_id: str,
        body: EditTurnBody,
        user_id: str = Depends(current_user_id),
    ):
        try:
            await self._service.edit(
                thread_id=thread_id,
                owner_id=user_id,
                message_id=body.message_id,
                content=body.content,
            )
        except ThreadNotFoundError:
            raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})
        except MessageNotFoundError:
            raise HTTPException(
                status_code=404,
                detail={"error": "MESSAGE_NOT_FOUND"},
            )
        except InvalidTurnTargetError as err:
            raise HTTPException(
                status_code=400,
                detail={"error": "INVALID_TARGET", "message": str(err)},
            )
        except TurnInProgressError:
            raise HTTPException(
                status_code=409,
                detail={"error": "TURN_IN_PROGRESS"},
            )
