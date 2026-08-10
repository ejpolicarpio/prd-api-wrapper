from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.configuration import Settings


def create_database_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.POSTGRESQL_URI,
        echo=settings.POSTGRESQL_ECHO,
        # Postgres drops idle connections and the pool cannot tell until it
        # tries to use one; pre_ping trades a cheap round trip for that class
        # of intermittent failure disappearing.
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": settings.POSTGRESQL_SCHEMA}},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        # Without this, attributes expire on commit and touching one afterwards
        # triggers a lazy reload -- which in async code raises rather than
        # quietly issuing SQL.
        expire_on_commit=False,
    )
