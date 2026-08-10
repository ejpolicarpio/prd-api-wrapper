from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.configuration import EnvironmentEnum, Settings
from src.databases.session import create_database_engine, create_session_factory
from src.endpoints.completion import router as completion_router
from src.endpoints.health import router as health_router
from src.endpoints.jobs import router as jobs_router
from src.errors.handlers import register_error_handlers
from src.logs import configure_logging
from src.middleware.request_id import RequestIdMiddleware
from src.services.rate_limiter import InMemoryRateLimiter, RateLimiter
from src.services.resilience import CircuitBreaker, RetryPolicy


class Application(FastAPI):
    settings: Settings
    http_client: httpx.AsyncClient
    retry_policy: RetryPolicy
    circuit_breaker: CircuitBreaker
    rate_limiter: RateLimiter
    postgresql_async_session: async_sessionmaker[AsyncSession]
    webhook_client: httpx.AsyncClient
    webhook_retry: RetryPolicy


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

        app.retry_policy = RetryPolicy(
            max_attempts=settings.RETRY_MAX_ATTEMPTS,
            initial_backoff=settings.RETRY_INITIAL_BACKOFF_SECONDS,
            max_backoff=settings.RETRY_MAX_BACKOFF_SECONDS,
            budget=settings.RETRY_BUDGET_SECONDS,
        )

        # Shared deliberately: the breaker only works if every request sees the
        # same failure count.
        app.circuit_breaker = CircuitBreaker(
            failure_threshold=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            reset_timeout=settings.CIRCUIT_BREAKER_RESET_SECONDS,
        )

        # Shared for the same reason as the breaker: a per-request limiter
        # would hand every request a full bucket.
        app.rate_limiter = InMemoryRateLimiter(
            capacity=settings.RATE_LIMIT_BURST,
            refill_rate=settings.RATE_LIMIT_REQUESTS_PER_MINUTE / 60,
        )

        # A separate client for callbacks, deliberately: the upstream one
        # carries the provider's Authorization header, and sending that to a
        # URL the caller chose would hand them our key.
        app.webhook_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.WEBHOOK_TIMEOUT_SECONDS, connect=5.0),
            follow_redirects=False,
        )

        app.webhook_retry = RetryPolicy(
            max_attempts=settings.WEBHOOK_MAX_ATTEMPTS,
            initial_backoff=settings.RETRY_INITIAL_BACKOFF_SECONDS,
            max_backoff=settings.RETRY_MAX_BACKOFF_SECONDS,
            budget=settings.RETRY_BUDGET_SECONDS,
        )

        # The engine owns the connection pool, so it is built once here; the
        # factory it produces hands out one short-lived session per request.
        engine = create_database_engine(settings)
        app.postgresql_async_session = create_session_factory(engine)

        yield

        await app.http_client.aclose()
        await app.webhook_client.aclose()
        await engine.dispose()

    return lifespan


def check_production_settings(settings: Settings) -> None:
    """Refuse to start misconfigured rather than start insecure.

    An empty signing secret still produces a signature, so deliveries would
    look signed while being forgeable by anyone. Failing at boot makes that a
    deploy failure instead of a silent vulnerability.
    """
    if settings.ENVIRONMENT is not EnvironmentEnum.PRODUCTION:
        return

    if not settings.WEBHOOK_SIGNING_SECRET:
        raise ValueError(
            "WEBHOOK_SIGNING_SECRET must be set outside local development."
        )


def create_app(settings: Settings | None = None) -> Application:
    _settings = settings or Settings()

    configure_logging(_settings)
    check_production_settings(_settings)

    _app = Application(debug=_settings.DEBUG, lifespan=lifespan_provider(_settings))
    _app.settings = _settings

    # Added first so it wraps everything else: middleware runs outermost-first,
    # and an id is only useful if it covers the layers that can fail.
    _app.add_middleware(RequestIdMiddleware)

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
    _app.include_router(jobs_router)

    return _app
