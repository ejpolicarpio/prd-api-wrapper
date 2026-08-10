from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.configuration import Settings
from src.repositories.api_keys import ApiKeyRepository, PostgresApiKeyRepository
from src.repositories.usage import PostgresUsageRepository, UsageRepository
from src.services.completion import CompletionService
from src.services.resilience import CircuitBreaker, RetryPolicy


def get_settings(request: Request) -> Settings:
    return request.app.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request: commit if the handler returned, roll back if
    it raised.

    Tying the transaction to the request means a half-finished write cannot
    survive a failure -- the caller got an error, so the database should look
    as though the request never happened.
    """
    async with request.app.postgresql_async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_api_key_repository(session: SessionDep) -> ApiKeyRepository:
    return PostgresApiKeyRepository(session)


def get_usage_repository(request: Request) -> UsageRepository:
    # The factory, not the request's session: usage must survive a rollback.
    return PostgresUsageRepository(request.app.postgresql_async_session)


ApiKeyRepositoryDep = Annotated[ApiKeyRepository, Depends(get_api_key_repository)]
UsageRepositoryDep = Annotated[UsageRepository, Depends(get_usage_repository)]


def get_http_client(request: Request) -> httpx.AsyncClient:
    """The single client built at startup -- never create one per request."""
    return request.app.http_client


def get_retry_policy(request: Request) -> RetryPolicy:
    return request.app.retry_policy


def get_circuit_breaker(request: Request) -> CircuitBreaker:
    """Shared across requests: its failure count is the whole point."""
    return request.app.circuit_breaker


SettingsDep = Annotated[Settings, Depends(get_settings)]
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]
RetryPolicyDep = Annotated[RetryPolicy, Depends(get_retry_policy)]
CircuitBreakerDep = Annotated[CircuitBreaker, Depends(get_circuit_breaker)]


def get_completion_service(
    client: HttpClientDep,
    settings: SettingsDep,
    retry: RetryPolicyDep,
    breaker: CircuitBreakerDep,
    usage: UsageRepositoryDep,
) -> CompletionService:
    return CompletionService(
        client=client,
        settings=settings,
        retry=retry,
        breaker=breaker,
        usage=usage,
    )


CompletionServiceDep = Annotated[CompletionService, Depends(get_completion_service)]
