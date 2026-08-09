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
