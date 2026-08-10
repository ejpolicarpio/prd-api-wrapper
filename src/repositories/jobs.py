from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.job import JobStatus, JobView
from src.models.tables import Job


class JobRepository(Protocol):
    async def create(self, job: Job) -> None: ...

    async def update(self, job_id: str, **fields: Any) -> None: ...

    async def get(self, job_id: str, api_key_id: str) -> JobView | None: ...

    async def prompt_for(self, job_id: str) -> tuple[str, str, str] | None: ...


def to_view(job: Job) -> JobView:
    return JobView(
        id=job.id,
        status=JobStatus(job.status),
        model=job.model,
        content=job.content,
        error_code=job.error_code,
        error_message=job.error_message,
        delivery_attempts=job.delivery_attempts,
        delivered_at=job.delivered_at,
        created_at=job.created_at,
    )


class PostgresJobRepository:
    """Session-per-operation, because a job outlives the request that made it.

    The background task that runs the job has no request session to borrow --
    the response was sent and its transaction closed long before.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, job: Job) -> None:
        async with self._session_factory() as session:
            session.add(job)
            await session.commit()

    async def update(self, job_id: str, **fields: Any) -> None:
        async with self._session_factory() as session:
            job = await session.get(Job, job_id)

            if job is None:
                return

            for field, value in fields.items():
                setattr(job, field, value)

            await session.commit()

    async def get(self, job_id: str, api_key_id: str) -> JobView | None:
        # Scoped by caller in the query: a job id must never be enough on its
        # own to read someone else's job.
        statement = select(Job).where(Job.id == job_id, Job.api_key_id == api_key_id)

        async with self._session_factory() as session:
            job = (await session.execute(statement)).scalar_one_or_none()

            return to_view(job) if job else None

    async def prompt_for(self, job_id: str) -> tuple[str, str, str] | None:
        """The stored request, for the worker: (prompt, model, callback_url)."""
        async with self._session_factory() as session:
            job = await session.get(Job, job_id)

            return (job.prompt, job.model, job.callback_url) if job else None


class InMemoryJobRepository:
    """Used by the fast suite."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def create(self, job: Job) -> None:
        # Column defaults and server defaults only fire on INSERT, so a
        # transient instance still has None where Postgres would have a value.
        job.created_at = job.created_at or datetime.now(UTC)
        job.delivery_attempts = job.delivery_attempts or 0
        job.prompt_tokens = job.prompt_tokens or 0
        job.completion_tokens = job.completion_tokens or 0

        self.jobs[job.id] = job

    async def update(self, job_id: str, **fields: Any) -> None:
        job = self.jobs.get(job_id)

        if job is None:
            return

        for field, value in fields.items():
            setattr(job, field, value)

    async def get(self, job_id: str, api_key_id: str) -> JobView | None:
        job = self.jobs.get(job_id)

        if job is None or job.api_key_id != api_key_id:
            return None

        return to_view(job)

    async def prompt_for(self, job_id: str) -> tuple[str, str, str] | None:
        job = self.jobs.get(job_id)

        return (job.prompt, job.model, job.callback_url) if job else None
