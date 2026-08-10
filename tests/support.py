"""Shared wiring for the fast suite, which runs without Postgres.

The repositories are the only things that touch the database, so overriding
those two dependencies keeps every other layer -- routing, auth, rate limits,
retries, error mapping -- running exactly as it does in production, with no
session ever opened.
"""

from collections.abc import Iterable

from fastapi.testclient import TestClient

from src.configuration import Settings
from src.dependencies.common import get_api_key_repository, get_usage_repository
from src.factory import create_app
from src.models.caller import ApiKeyRecord
from src.repositories.api_keys import InMemoryApiKeyRepository
from src.repositories.usage import InMemoryUsageRepository

UPSTREAM_BASE_URL = "http://upstream.test/v1"
UPSTREAM_ROUTE = f"{UPSTREAM_BASE_URL}/chat/completions"

OK_BODY = {
    "id": "chatcmpl-1",
    "model": "test-model",
    "choices": [{"message": {"content": "hello"}}],
    "usage": {"prompt_tokens": 7, "completion_tokens": 3},
}


def build_test_client(
    *,
    api_keys: Iterable[ApiKeyRecord] = (),
    usage: InMemoryUsageRepository | None = None,
    **overrides,
) -> TestClient:
    defaults = {
        "UPSTREAM_BASE_URL": UPSTREAM_BASE_URL,
        "UPSTREAM_MODEL": "test-model",
    }

    app = create_app(Settings(**(defaults | overrides)))

    keys_repository = InMemoryApiKeyRepository(api_keys)
    usage_repository = usage or InMemoryUsageRepository()

    app.dependency_overrides[get_api_key_repository] = lambda: keys_repository
    app.dependency_overrides[get_usage_repository] = lambda: usage_repository

    return TestClient(app, raise_server_exceptions=False)
