from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    """Aggregate root for a user's project.

    `github_encrypted_token` stores the Fernet-encrypted bytes of the
    raw GitHub personal-access-token.  The application layer is the
    only place that encrypts/decrypts — the repository and routes never
    see plaintext tokens.
    """

    __tablename__ = "projects"

    # Postgres UUID primary key. `default` runs in Python (before the
    # INSERT) so the value is available on the object without a round-trip.
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )

    # The identity of the owning user.  Comes from the upstream auth
    # service (JWT sub / X-User-Id header) — stored as opaque string so
    # the service is auth-backend-agnostic.
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional link to a GitHub repository (https://github.com/org/repo).
    github_repo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Fernet-encrypted GitHub personal-access-token bytes.  NULL when no
    # token has been stored (public repos, or token not yet supplied).
    github_encrypted_token: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )

    # Identifier of the E2B / Daytona sandbox associated with this project.
    # NULL until the orchestrator assigns one.
    sandbox_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # Composite index for the most common query pattern: "all projects
    # belonging to a user ordered by recency".
    __table_args__ = (
        Index("ix_projects_user_id_created_at", "user_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project id={self.id} name={self.name!r} user_id={self.user_id!r}>"
