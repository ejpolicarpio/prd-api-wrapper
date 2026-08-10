from enum import StrEnum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentEnum(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    ROOT_DIR: Path = Path(__file__).resolve().parent.parent
    ENVIRONMENT: EnvironmentEnum = EnvironmentEnum.PRODUCTION
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    DEBUG: bool = False

    # Upstream LLM provider (OpenAI-compatible; Ollama by default)
    # ========================================================
    UPSTREAM_BASE_URL: str = "http://localhost:11434/v1"
    UPSTREAM_API_KEY: str = "ollama"
    UPSTREAM_MODEL: str = "llama3.2:3b"
    UPSTREAM_TIMEOUT_SECONDS: float = 60.0
    UPSTREAM_CONNECT_TIMEOUT_SECONDS: float = 2.0

    # Database (PostgreSQL)
    # ========================================================
    POSTGRESQL_USERNAME: str = "postgres"
    POSTGRESQL_PASSWORD: str = "postgres"
    POSTGRESQL_HOST: str = "localhost"
    POSTGRESQL_PORT: int = 5432
    POSTGRESQL_DB: str = "postgres"
    POSTGRESQL_ECHO: bool = False
    POSTGRESQL_SCHEMA: str = "public"

    @property
    def POSTGRESQL_URI(self) -> str:
        # asyncpg, not psycopg: create_async_engine needs an async driver, and
        # asyncpg is the one this project depends on.
        return (
            f"postgresql+asyncpg://{self.POSTGRESQL_USERNAME}:{self.POSTGRESQL_PASSWORD}"
            f"@{self.POSTGRESQL_HOST}:{self.POSTGRESQL_PORT}/{self.POSTGRESQL_DB}"
        )

    # Authentication
    # ========================================================
    # Fails closed: keys live in the database, so an empty table lets nobody
    # through. Set REQUIRE_API_KEY=false for local poking around.
    REQUIRE_API_KEY: bool = True

    # Webhooks
    # ========================================================
    # Callers verify deliveries with this. Per-caller secrets would be better
    # -- one leak would then compromise one client, not all of them.
    WEBHOOK_SIGNING_SECRET: str = ""
    WEBHOOK_TIMEOUT_SECONDS: float = 10.0
    WEBHOOK_MAX_ATTEMPTS: int = 4

    # Rate limiting
    # ========================================================
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 60
    # Burst is the bucket's capacity: how many requests a caller who has been
    # idle may fire at once before the steady rate applies.
    RATE_LIMIT_BURST: int = 10

    # Resilience
    # ========================================================
    RETRY_MAX_ATTEMPTS: int = 3
    RETRY_INITIAL_BACKOFF_SECONDS: float = 0.5
    RETRY_MAX_BACKOFF_SECONDS: float = 8.0
    RETRY_BUDGET_SECONDS: float = 30.0
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_RESET_SECONDS: float = 30.0
