from typing import Annotated

import httpx
from fastapi import Depends, Request

from src.configuration import Settings
from src.services.completion import CompletionService


def get_settings(request: Request) -> Settings:
    return request.app.settings


def get_http_client(request: Request) -> httpx.AsyncClient:
    """The single client built at startup -- never create one per request."""
    return request.app.http_client


SettingsDep = Annotated[Settings, Depends(get_settings)]
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


def get_completion_service(
    client: HttpClientDep, settings: SettingsDep
) -> CompletionService:
    return CompletionService(client=client, settings=settings)


CompletionServiceDep = Annotated[CompletionService, Depends(get_completion_service)]
