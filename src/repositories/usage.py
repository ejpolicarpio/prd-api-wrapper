from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.tables import Usage
from src.models.usage import UsageEntry


class UsageRepository(Protocol):
    async def record(self, entry: UsageEntry) -> None: ...


class PostgresUsageRepository:
    """Writes usage in its own transaction, not the request's.

    A failed request rolls its session back, which would take the usage row
    with it -- losing exactly the records you most want, since an outage is
    when you care who was calling. So this opens a short transaction of its
    own and commits it independently of how the request ends.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, entry: UsageEntry) -> None:
        async with self._session_factory() as session:
            session.add(
                Usage(
                    api_key_id=entry.api_key_id,
                    model=entry.model,
                    prompt_tokens=entry.prompt_tokens,
                    completion_tokens=entry.completion_tokens,
                    status_code=entry.status_code,
                    duration_ms=entry.duration_ms,
                )
            )
            await session.commit()


class InMemoryUsageRepository:
    """Used by the fast test suite; also handy for asserting on what was
    recorded without reaching for SQL."""

    def __init__(self) -> None:
        self.entries: list[UsageEntry] = []

    async def record(self, entry: UsageEntry) -> None:
        self.entries.append(entry)
