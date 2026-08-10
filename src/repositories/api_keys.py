from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.caller import ApiKeyRecord, Caller
from src.models.tables import ApiKey


class ApiKeyRepository(Protocol):
    async def find_by_hash(self, key_hash: str) -> Caller | None: ...

    async def add(self, record: ApiKeyRecord) -> None: ...


class PostgresApiKeyRepository:
    """Keys as stored rows.

    Lookup is by digest against a unique index, so it stays a single indexed
    read no matter how many keys exist.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_hash(self, key_hash: str) -> Caller | None:
        # Revocation is part of the query rather than a check afterwards: a
        # revoked key must be indistinguishable from one that never existed.
        statement = select(ApiKey).where(
            ApiKey.key_hash == key_hash.lower(),
            ApiKey.revoked_at.is_(None),
        )

        api_key = (await self._session.execute(statement)).scalar_one_or_none()

        if api_key is None:
            return None

        await self._session.execute(
            update(ApiKey)
            .where(ApiKey.id == api_key.id)
            .values(last_used_at=datetime.now(UTC))
        )

        return Caller(id=api_key.id, name=api_key.name)

    async def add(self, record: ApiKeyRecord) -> None:
        self._session.add(
            ApiKey(
                id=record.id,
                name=record.name,
                key_hash=record.key_hash.lower(),
            )
        )


class InMemoryApiKeyRepository:
    """Used by the fast test suite, which has no database.

    Lookup is a dict keyed by digest, so no secret is ever compared byte by
    byte -- which sidesteps timing attacks rather than defending against them.
    """

    def __init__(self, records: Iterable[ApiKeyRecord] = ()) -> None:
        self._callers_by_hash: dict[str, Caller] = {}

        for record in records:
            self._callers_by_hash[record.key_hash.lower()] = Caller(
                id=record.id, name=record.name
            )

    async def find_by_hash(self, key_hash: str) -> Caller | None:
        return self._callers_by_hash.get(key_hash.lower())

    async def add(self, record: ApiKeyRecord) -> None:
        self._callers_by_hash[record.key_hash.lower()] = Caller(
            id=record.id, name=record.name
        )
