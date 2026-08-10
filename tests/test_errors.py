import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from tests.support import UPSTREAM_ROUTE, build_test_client


@pytest.fixture
def client():
    # These tests are about mapping a failure onto our contract, not about
    # repeating it -- retries are covered in test_completion_retries.py.
    with build_test_client(RETRY_MAX_ATTEMPTS=1, REQUIRE_API_KEY=False) as test_client:
        yield test_client


def complete(client: TestClient) -> httpx.Response:
    return client.post("/v1/complete", json={"prompt": "hi"})


@respx.mock
def test_unknown_model_is_a_client_error(client: TestClient) -> None:
    """Ollama answers 404 for a model it doesn't have -- the caller's mistake."""
    respx.post(UPSTREAM_ROUTE).mock(
        return_value=httpx.Response(
            404, json={"error": {"message": "model 'nope' not found"}}
        )
    )

    response = client.post("/v1/complete", json={"prompt": "hi", "model": "nope"})

    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "model_not_found"
    assert body["message"] == "model 'nope' not found"
    assert body["details"] == {"model": "nope"}


@respx.mock
def test_upstream_rate_limit_is_passed_through_with_retry_after(
    client: TestClient,
) -> None:
    respx.post(UPSTREAM_ROUTE).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "7"}, json={})
    )

    response = complete(client)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert response.json()["error"]["code"] == "upstream_rate_limited"


@respx.mock
def test_upstream_auth_failure_does_not_leak_to_the_caller(client: TestClient) -> None:
    """Our key is wrong, not theirs -- so no 401 and no upstream message."""
    respx.post(UPSTREAM_ROUTE).mock(
        return_value=httpx.Response(
            401, json={"error": {"message": "invalid api key sk-abc123"}}
        )
    )

    response = complete(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_auth_failed"
    assert "sk-abc123" not in response.text


@respx.mock
def test_upstream_server_error_becomes_bad_gateway(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(500, text="boom"))

    response = complete(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


@respx.mock
def test_timeout_becomes_gateway_timeout(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(side_effect=httpx.ReadTimeout("too slow"))

    response = complete(client)

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"


@respx.mock
def test_connection_failure_becomes_service_unavailable(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(side_effect=httpx.ConnectError("refused"))

    response = complete(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_unavailable"


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        {"id": "x", "model": "test-model"},  # no choices
        {"id": "x", "model": "test-model", "choices": []},  # empty choices
        {"id": "x", "model": "test-model", "choices": [{}]},  # no message
        ["not", "an", "object"],  # not even a mapping
    ],
)
def test_malformed_upstream_body_is_not_a_crash(client: TestClient, body) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=body))

    response = complete(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_upstream_response"


@respx.mock
def test_non_json_upstream_body_is_not_a_crash(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, text="<html>"))

    response = complete(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_upstream_response"


def test_validation_errors_use_the_same_envelope(client: TestClient) -> None:
    response = client.post("/v1/complete", json={"prompt": ""})

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert body["details"]["fields"][0]["loc"] == ["body", "prompt"]
