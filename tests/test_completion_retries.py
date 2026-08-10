import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from tests.support import OK_BODY, UPSTREAM_ROUTE, build_test_client


def build_client(**overrides) -> TestClient:
    # Zero backoff keeps the suite fast; the delay arithmetic itself is covered
    # by unit tests in test_resilience.py.
    defaults = {
        "RETRY_INITIAL_BACKOFF_SECONDS": 0.0,
        "RETRY_MAX_ATTEMPTS": 3,
        "CIRCUIT_BREAKER_FAILURE_THRESHOLD": 100,
        "REQUIRE_API_KEY": False,
    }

    return build_test_client(**(defaults | overrides))


@pytest.fixture
def client():
    with build_client() as test_client:
        yield test_client


def complete(client: TestClient) -> httpx.Response:
    return client.post("/v1/complete", json={"prompt": "hi"})


@respx.mock
def test_transient_failure_is_retried_and_the_caller_never_sees_it(
    client: TestClient,
) -> None:
    route = respx.post(UPSTREAM_ROUTE).mock(
        side_effect=[
            httpx.Response(503, json={}),
            httpx.Response(200, json=OK_BODY),
        ]
    )

    response = complete(client)

    assert response.status_code == 200
    assert response.json()["content"] == "hello"
    assert route.call_count == 2


@respx.mock
def test_rate_limit_is_retried(client: TestClient) -> None:
    route = respx.post(UPSTREAM_ROUTE).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, json={}),
            httpx.Response(200, json=OK_BODY),
        ]
    )

    assert complete(client).status_code == 200
    assert route.call_count == 2


@respx.mock
def test_timeouts_are_retried(client: TestClient) -> None:
    route = respx.post(UPSTREAM_ROUTE).mock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.Response(200, json=OK_BODY),
        ]
    )

    assert complete(client).status_code == 200
    assert route.call_count == 2


@respx.mock
def test_a_rejected_request_is_never_retried(client: TestClient) -> None:
    """A 404 for an unknown model will be a 404 every time -- don't waste quota."""
    route = respx.post(UPSTREAM_ROUTE).mock(
        return_value=httpx.Response(404, json={"error": {"message": "no such model"}})
    )

    response = client.post("/v1/complete", json={"prompt": "hi", "model": "nope"})

    assert response.status_code == 400
    assert route.call_count == 1


@respx.mock
def test_persistent_failure_surfaces_after_the_attempt_cap(client: TestClient) -> None:
    route = respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(500, json={}))

    response = complete(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
    assert route.call_count == 3


@respx.mock
def test_circuit_opens_and_then_fails_without_calling_the_provider() -> None:
    """Two requests x three attempts trips a threshold of six; the third
    request must not reach the provider at all."""
    with build_client(CIRCUIT_BREAKER_FAILURE_THRESHOLD=6) as client:
        route = respx.post(UPSTREAM_ROUTE).mock(
            return_value=httpx.Response(500, json={})
        )

        assert complete(client).status_code == 502
        assert complete(client).status_code == 502
        assert route.call_count == 6

        response = complete(client)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "upstream_circuit_open"
        assert route.call_count == 6  # no further calls were made
