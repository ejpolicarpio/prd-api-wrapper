import pytest

from src.models.job import JobStatus
from src.models.tables import ApiKey, Job
from src.repositories.jobs import PostgresJobRepository
from src.services.authentication import mint_key

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def seed_caller(session, caller_id: str = "caller-a") -> str:
    _, record = mint_key("Client")
    session.add(ApiKey(id=caller_id, name="Client", key_hash=record.key_hash))
    await session.commit()

    return caller_id


def make_job(job_id: str, caller_id: str) -> Job:
    return Job(
        id=job_id,
        api_key_id=caller_id,
        status=JobStatus.PENDING,
        model="llama3.2:3b",
        prompt="hi",
        callback_url="https://client.test/hooks",
    )


async def test_a_job_round_trips(session, session_factory) -> None:
    caller_id = await seed_caller(session)
    repository = PostgresJobRepository(session_factory)

    await repository.create(make_job("job-1", caller_id))

    view = await repository.get("job-1", caller_id)

    assert view is not None
    assert view.status is JobStatus.PENDING
    assert view.delivery_attempts == 0
    assert view.created_at is not None  # server default fired


async def test_updates_are_persisted(session, session_factory) -> None:
    caller_id = await seed_caller(session)
    repository = PostgresJobRepository(session_factory)
    await repository.create(make_job("job-2", caller_id))

    await repository.update(
        "job-2", status=JobStatus.SUCCEEDED, content="hello", completion_tokens=3
    )

    view = await repository.get("job-2", caller_id)

    assert view is not None
    assert view.status is JobStatus.SUCCEEDED
    assert view.content == "hello"


async def test_a_job_is_scoped_to_its_caller(session, session_factory) -> None:
    """The query filters by caller, so an id alone is never enough."""
    caller_id = await seed_caller(session, "caller-a")
    await seed_caller(session, "caller-b")

    repository = PostgresJobRepository(session_factory)
    await repository.create(make_job("job-3", caller_id))

    assert await repository.get("job-3", "caller-b") is None
    assert await repository.get("job-3", caller_id) is not None


async def test_updating_a_missing_job_is_not_an_error(session_factory) -> None:
    """The worker updates by id; a vanished row should not crash it."""
    await PostgresJobRepository(session_factory).update("nope", status="failed")


async def test_the_worker_can_read_back_what_it_needs(session, session_factory) -> None:
    caller_id = await seed_caller(session)
    repository = PostgresJobRepository(session_factory)
    await repository.create(make_job("job-4", caller_id))

    assert await repository.prompt_for("job-4") == (
        "hi",
        "llama3.2:3b",
        "https://client.test/hooks",
    )
