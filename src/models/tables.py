from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import BaseModel
from src.models.job import JobStatus


class ApiKey(BaseModel):
    """A credential we issued. Table name comes from BaseModel: api_key."""

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))

    # Unique so the same key cannot be registered twice, and indexed because
    # every single authenticated request looks a row up by this column.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Revocation is a timestamp rather than a delete: we still want the usage
    # rows that point here to resolve to something.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class Usage(BaseModel):
    """One row per request, successful or not. Table name: usage."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # No index=True here: the composite index below already covers lookups by
    # api_key_id, because Postgres can use a composite index for queries on its
    # leading column. A second index would cost writes and buy nothing.
    api_key_id: Mapped[str] = mapped_column(String(32), ForeignKey("api_key.id"))

    model: Mapped[str] = mapped_column(String(255))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Recorded whether the request succeeded or failed: an outage is exactly
    # when you want to know who was calling.
    status_code: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        # "what did this caller spend recently" is the query this table exists
        # to answer, so it gets a composite index rather than two separate ones.
        Index("ix_usage_api_key_id_created_at", "api_key_id", "created_at"),
    )


class Job(BaseModel):
    """Work accepted now and answered later. Table name: job."""

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    api_key_id: Mapped[str] = mapped_column(String(32), ForeignKey("api_key.id"))

    # Stored as its value rather than a database enum: adding a status later
    # then needs no ALTER TYPE, which is a migration that locks the table.
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.PENDING)

    model: Mapped[str] = mapped_column(String(255))
    prompt: Mapped[str] = mapped_column(Text)
    callback_url: Mapped[str] = mapped_column(String(2048))

    content: Mapped[str | None] = mapped_column(Text, default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)

    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Every read is "this caller's jobs", newest first -- a job must never
        # be visible to anyone but the caller who created it.
        Index("ix_job_api_key_id_created_at", "api_key_id", "created_at"),
    )
