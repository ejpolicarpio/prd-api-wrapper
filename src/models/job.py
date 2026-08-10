from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_final(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED)


class JobRequest(BaseModel):
    """Same as a completion, plus where to send the answer."""

    prompt: str = Field(min_length=1, max_length=8000, examples=["Say hello."])
    model: str | None = Field(default=None, examples=["llama3.2:3b"])
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=4096)

    # HttpUrl rejects anything that isn't http(s) at the edge, so we never hold
    # a job whose callback could not be delivered.
    callback_url: HttpUrl = Field(examples=["https://example.com/hooks/llm"])


class JobAccepted(BaseModel):
    """The 202 body: an id to poll or to match the webhook against."""

    id: str
    status: JobStatus


class JobView(BaseModel):
    """What GET /v1/jobs/{id} returns."""

    id: str
    status: JobStatus
    model: str
    content: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    delivery_attempts: int = 0
    delivered_at: datetime | None = None
    created_at: datetime


class WebhookPayload(BaseModel):
    """The body we sign and POST to the caller.

    `job_id` is the idempotency key: a receiver that sees the same one twice
    -- which a delivery retry can cause -- should treat the second as a
    duplicate rather than as new work.
    """

    job_id: str
    status: JobStatus
    model: str
    content: str | None = None
    error_code: str | None = None
    error_message: str | None = None
