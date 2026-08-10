from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import BaseModel


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
