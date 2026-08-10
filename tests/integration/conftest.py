"""Fixtures for tests that talk to a real Postgres.

Each test runs inside a transaction that is rolled back afterwards, so the
tests share one database without sharing any state and nothing needs cleaning
up between them.
"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.configuration import Settings
from src.databases.session import create_database_engine
from src.models import tables  # noqa: F401  (registers the tables)
from src.models.base import BaseModel


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Always a database of its own, never the one the app uses.

    These tests drop every table on setup. Pointing them at the development
    database -- which is what happens if this override is removed -- deletes
    real data, so the isolation is enforced here rather than left to whoever
    runs the command.
    """
    base = Settings()

    return base.model_copy(update={"POSTGRESQL_DB": f"{base.POSTGRESQL_DB}_test"})


async def ensure_database_exists(settings: Settings) -> None:
    """CREATE DATABASE cannot run inside a transaction, hence AUTOCOMMIT."""
    admin_settings = settings.model_copy(update={"POSTGRESQL_DB": "postgres"})
    admin_engine = create_async_engine(
        admin_settings.POSTGRESQL_URI, isolation_level="AUTOCOMMIT"
    )

    try:
        async with admin_engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": settings.POSTGRESQL_DB},
            )

            if not exists:
                await connection.execute(
                    text(f'CREATE DATABASE "{settings.POSTGRESQL_DB}"')
                )
    finally:
        await admin_engine.dispose()


# loop_scope="session" everywhere below: the engine holds a connection pool
# bound to the loop it was created on, so tests must run on that same loop or
# they inherit a pool whose loop has already been closed.
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine(settings: Settings):
    await ensure_database_exists(settings)

    engine = create_database_engine(settings)

    # Build the schema from the models rather than running migrations, so a
    # broken migration fails its own test instead of every test.
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.drop_all)
        await connection.run_sync(BaseModel.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def connection(engine):
    """One connection per test, wrapped in a transaction that never commits.

    Everything the test does lands inside it -- including code that commits,
    which SQLAlchemy turns into a savepoint -- so the rollback at the end
    leaves the database exactly as it was found.
    """
    connection = await engine.connect()
    transaction = await connection.begin()

    try:
        yield connection
    finally:
        # A test that provoked an IntegrityError has already lost the
        # transaction, so only roll back one that is still live.
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture(loop_scope="session")
async def session_factory(connection) -> async_sessionmaker[AsyncSession]:
    """For code that opens its own sessions, bound to the test's connection."""
    return async_sessionmaker(bind=connection, expire_on_commit=False)


@pytest_asyncio.fixture(loop_scope="session")
async def session(session_factory) -> AsyncSession:
    session = session_factory()

    try:
        yield session
    finally:
        await session.close()
