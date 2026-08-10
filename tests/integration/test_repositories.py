from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.caller import hash_api_key
from src.models.tables import ApiKey, Usage
from src.models.usage import UsageEntry
from src.repositories.api_keys import PostgresApiKeyRepository
from src.repositories.usage import PostgresUsageRepository
from src.services.authentication import mint_key

# Every test here shares the engine's event loop -- see conftest.
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def add_key(session: AsyncSession, name: str = "Client") -> tuple[str, str]:
    key, record = mint_key(name)
    await PostgresApiKeyRepository(session).add(record)
    await session.flush()

    return key, record.id


# API keys
# ========================================================


async def test_a_stored_key_resolves_to_its_caller(session: AsyncSession) -> None:
    key, key_id = await add_key(session, "Acme")

    caller = await PostgresApiKeyRepository(session).find_by_hash(hash_api_key(key))

    assert caller is not None
    assert caller.id == key_id
    assert caller.name == "Acme"


async def test_an_unknown_digest_resolves_to_nothing(session: AsyncSession) -> None:
    await add_key(session)

    repository = PostgresApiKeyRepository(session)

    assert await repository.find_by_hash(hash_api_key("sk-never-issued")) is None


async def test_a_revoked_key_stops_working(session: AsyncSession) -> None:
    """Revocation is the thing the database bought us over config keys."""
    key, key_id = await add_key(session)
    repository = PostgresApiKeyRepository(session)

    assert await repository.find_by_hash(hash_api_key(key)) is not None

    stored = await session.get(ApiKey, key_id)
    assert stored is not None
    stored.revoked_at = datetime.now(UTC)
    await session.flush()

    assert await repository.find_by_hash(hash_api_key(key)) is None


async def test_using_a_key_records_when(session: AsyncSession) -> None:
    key, key_id = await add_key(session)

    assert (await session.get(ApiKey, key_id)).last_used_at is None

    await PostgresApiKeyRepository(session).find_by_hash(hash_api_key(key))
    await session.flush()
    session.expire_all()

    assert (await session.get(ApiKey, key_id)).last_used_at is not None


async def test_the_same_digest_cannot_be_stored_twice(session: AsyncSession) -> None:
    """The unique index is what stops a key being registered to two callers."""
    _, record = mint_key("First")
    repository = PostgresApiKeyRepository(session)

    await repository.add(record)
    await session.flush()

    duplicate = record.model_copy(update={"id": "different-id"})
    await repository.add(duplicate)

    with pytest.raises(IntegrityError):
        await session.flush()


# Usage
# ========================================================


async def test_usage_rows_are_written(session: AsyncSession, session_factory) -> None:
    _, key_id = await add_key(session)
    await session.commit()

    await PostgresUsageRepository(session_factory).record(
        UsageEntry(
            api_key_id=key_id,
            model="llama3.2:3b",
            status_code=200,
            duration_ms=1234,
            prompt_tokens=30,
            completion_tokens=2,
        )
    )
    await session.flush()

    stored = (await session.execute(select(Usage))).scalars().all()

    assert len(stored) == 1
    assert stored[0].api_key_id == key_id
    assert stored[0].prompt_tokens == 30
    assert stored[0].status_code == 200
    assert stored[0].created_at is not None  # server default fired


async def test_failed_requests_are_recorded_too(
    session: AsyncSession, session_factory
) -> None:
    """An outage is exactly when you want to know who was calling."""
    _, key_id = await add_key(session)
    await session.commit()

    await PostgresUsageRepository(session_factory).record(
        UsageEntry(
            api_key_id=key_id, model="llama3.2:3b", status_code=503, duration_ms=12
        )
    )
    await session.flush()

    stored = (await session.execute(select(Usage))).scalar_one()

    assert stored.status_code == 503
    assert stored.prompt_tokens == 0


async def test_usage_cannot_point_at_a_key_that_does_not_exist(session_factory) -> None:
    with pytest.raises(IntegrityError):
        await PostgresUsageRepository(session_factory).record(
            UsageEntry(
                api_key_id="no-such-key", model="m", status_code=200, duration_ms=1
            )
        )
