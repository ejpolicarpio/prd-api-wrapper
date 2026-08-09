from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.configuration import Settings
from src.endpoints.completion import router as completion_router
from src.endpoints.health import router as health_router
from src.errors.handlers import register_error_handlers


class Application(FastAPI):
    settings: Settings
    http_client: httpx.AsyncClient


def lifespan_provider(settings: Settings) -> Callable:
    @asynccontextmanager
    async def lifespan(app: Application) -> AsyncIterator[None]:
        # One connection-pooled client for the whole process, not one per request.
        app.http_client = httpx.AsyncClient(
            base_url=settings.UPSTREAM_BASE_URL,
            headers={"Authorization": f"Bearer {settings.UPSTREAM_API_KEY}"},
            timeout=httpx.Timeout(
                settings.UPSTREAM_TIMEOUT_SECONDS,
                connect=settings.UPSTREAM_CONNECT_TIMEOUT_SECONDS,
            ),
        )

        yield

        await app.http_client.aclose()

    return lifespan


def create_app(settings: Settings | None = None) -> Application:
    _settings = settings or Settings()

    _app = Application(debug=_settings.DEBUG, lifespan=lifespan_provider(_settings))
    _app.settings = _settings

    _app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(_app)

    _app.include_router(health_router)
    _app.include_router(completion_router)

    return _app
