import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from tests.support import UPSTREAM_BASE_URL, build_test_client


@pytest.fixture
def client():
    # Authentication has its own suite; these tests are about the contract.
    # The `with` block matters: it runs lifespan, which builds the http client.
    with build_test_client(REQUIRE_API_KEY=False) as test_client:
        yield test_client


def upstream_payload() -> dict:
    return {
        "id": "chatcmpl-123",
        "model": "test-model",
        "choices": [{"message": {"role": "assistant", "content": "hello there"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


@respx.mock
def test_complete_returns_our_shape(client: TestClient) -> None:
    route = respx.post(f"{UPSTREAM_BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(200, json=upstream_payload())
    )

    response = client.post("/v1/complete", json={"prompt": "hi"})

    assert response.status_code == 200
    assert response.json() == {
        "id": "chatcmpl-123",
        "model": "test-model",
        "content": "hello there",
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }

    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "test-model"  # default from settings
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


@respx.mock
def test_model_override_is_forwarded(client: TestClient) -> None:
    route = respx.post(f"{UPSTREAM_BASE_URL}/chat/completions").mock(
        return_value=httpx.Response(200, json=upstream_payload())
    )

    client.post("/v1/complete", json={"prompt": "hi", "model": "other-model"})

    assert json.loads(route.calls.last.request.content)["model"] == "other-model"


def test_empty_prompt_is_rejected_before_we_call_upstream(client: TestClient) -> None:
    assert client.post("/v1/complete", json={"prompt": ""}).status_code == 422
