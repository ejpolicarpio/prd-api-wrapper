from typing import Annotated

import httpx
from fastapi import Depends, Request

from src.configuration import Settings
from src.services.completion import CompletionService
from src.services.resilience import CircuitBreaker, RetryPolicy


def get_settings(request: Request) -> Settings:
    return request.app.settings


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
) -> CompletionService:
    return CompletionService(
        client=client, settings=settings, retry=retry, breaker=breaker
    )


CompletionServiceDep = Annotated[CompletionService, Depends(get_completion_service)]
