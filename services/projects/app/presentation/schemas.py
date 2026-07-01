from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


class ProjectOut(BaseModel):
    """Public representation of a project.

    IMPORTANT: `github_encrypted_token` is intentionally absent — callers
    must never receive the raw (or encrypted) token over the wire.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: str
    name: str
    description: str | None
    github_repo_url: str | None
    # Token presence indicator.  True when an encrypted token is stored;
    # False otherwise.  This lets the UI show "token configured" without
    # exposing the value or even its encrypted form.
    github_token_set: bool
    sandbox_id: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, project: object) -> "ProjectOut":  # type: ignore[override]
        # `from_orm` is deprecated in Pydantic v2; use model_validate instead.
        return cls.model_validate(project)

    @classmethod
    def from_model(cls, project: object) -> "ProjectOut":
        """Build a ProjectOut from a Project ORM instance.

        Computes `github_token_set` from the presence of the encrypted bytes
        field so the route layer doesn't need to know the field name.
        """
        from ..domain.models import Project as ProjectModel  # local import avoids cycles

        p: ProjectModel = project  # type: ignore[assignment]
        return cls(
            id=p.id,
            user_id=p.user_id,
            name=p.name,
            description=p.description,
            github_repo_url=p.github_repo_url,
            github_token_set=p.github_encrypted_token is not None,
            sandbox_id=p.sandbox_id,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4096)
    github_repo_url: str | None = Field(default=None, max_length=2048)
    # Plain-text token supplied by the user; encrypted before storage.
    github_token: str | None = Field(default=None, max_length=1024)


class ProjectUpdate(BaseModel):
    """All fields are optional — only supplied fields are applied."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=4096)
    github_repo_url: str | None = Field(default=None, max_length=2048)
    # Supplying `null` explicitly clears the stored token.
    # Omitting the field (not present in JSON) leaves the token unchanged.
    github_token: str | None = Field(default=None, max_length=1024)


class SandboxAssign(BaseModel):
    sandbox_id: str = Field(..., min_length=1, max_length=255)
