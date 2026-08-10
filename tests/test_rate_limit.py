import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from src.configuration import Settings
from src.factory import create_app
from src.models.caller import ApiKeyRecord, hash_api_key
from src.services.rate_limiter import InMemoryRateLimiter, TokenBucket

UPSTREAM_BASE_URL = "http://upstream.test/v1"
UPSTREAM_ROUTE = f"{UPSTREAM_BASE_URL}/chat/completions"

KEY_A = "sk-client-a"
KEY_B = "sk-client-b"

OK_BODY = {
    "id": "chatcmpl-1",
    "model": "test-model",
    "choices": [{"message": {"content": "hello"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# The bucket itself
# ========================================================


def test_a_fresh_bucket_allows_a_full_burst() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=3, refill_rate=1.0, clock=clock)

    assert [bucket.take().allowed for _ in range(3)] == [True, True, True]
    assert bucket.take().allowed is False


def test_tokens_come_back_over_time() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=3, refill_rate=1.0, clock=clock)

    for _ in range(3):
        bucket.take()
    assert bucket.take().allowed is False

    clock.advance(2.0)  # 2 tokens back at 1/s

    assert bucket.take().allowed is True
    assert bucket.take().allowed is True
    assert bucket.take().allowed is False


def test_idle_time_does_not_accumulate_beyond_the_cap() -> None:
    """An hour of silence buys a burst, not an hour's worth of requests."""
    clock = FakeClock()
    bucket = TokenBucket(capacity=3, refill_rate=1.0, clock=clock)

    clock.advance(3600.0)

    assert [bucket.take().allowed for _ in range(4)] == [True, True, True, False]


def test_a_refused_request_reports_when_to_come_back() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, refill_rate=0.5, clock=clock)  # 1 per 2s

    bucket.take()
    decision = bucket.take()

    assert decision.allowed is False
    assert decision.retry_after == pytest.approx(2.0)
    assert decision.remaining == 0


def test_an_allowed_request_reports_what_is_left() -> None:
    bucket = TokenBucket(capacity=5, refill_rate=1.0, clock=FakeClock())

    decision = bucket.take()

    assert decision.allowed is True
    assert decision.limit == 5
    assert decision.remaining == 4
    assert decision.retry_after == 0.0


async def test_each_caller_gets_their_own_bucket() -> None:
    limiter = InMemoryRateLimiter(capacity=1, refill_rate=1.0, clock=FakeClock())

    assert (await limiter.check("caller-a")).allowed is True
    assert (await limiter.check("caller-a")).allowed is False
    assert (await limiter.check("caller-b")).allowed is True


# Through the API
# ========================================================


def build_client(**overrides) -> TestClient:
    defaults = {
        "UPSTREAM_BASE_URL": UPSTREAM_BASE_URL,
        "UPSTREAM_MODEL": "test-model",
        "RETRY_MAX_ATTEMPTS": 1,
        "RATE_LIMIT_BURST": 2,
        "RATE_LIMIT_REQUESTS_PER_MINUTE": 60,
        "API_KEYS": [
            ApiKeyRecord(id="a", name="Client A", key_hash=hash_api_key(KEY_A)),
            ApiKeyRecord(id="b", name="Client B", key_hash=hash_api_key(KEY_B)),
        ],
    }

    return TestClient(
        create_app(Settings(**(defaults | overrides))), raise_server_exceptions=False
    )


@pytest.fixture
def client():
    with build_client() as test_client:
        yield test_client


def complete(client: TestClient, key: str) -> httpx.Response:
    return client.post(
        "/v1/complete",
        json={"prompt": "hi"},
        headers={"Authorization": f"Bearer {key}"},
    )


@respx.mock
def test_a_caller_over_the_limit_is_refused(client: TestClient) -> None:
    route = respx.post(UPSTREAM_ROUTE).mock(
        return_value=httpx.Response(200, json=OK_BODY)
    )

    assert complete(client, KEY_A).status_code == 200
    assert complete(client, KEY_A).status_code == 200

    response = complete(client, KEY_A)

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"
    assert int(response.headers["retry-after"]) >= 1
    assert response.headers["x-ratelimit-remaining"] == "0"
    assert route.call_count == 2  # the refused request never reached the provider


@respx.mock
def test_allowed_responses_carry_the_budget_headers(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))

    response = complete(client, KEY_A)

    assert response.headers["x-ratelimit-limit"] == "2"
    assert response.headers["x-ratelimit-remaining"] == "1"
    assert "x-ratelimit-reset" in response.headers


@respx.mock
def test_one_caller_cannot_exhaust_another(client: TestClient) -> None:
    """The whole point of keying on identity rather than on the endpoint."""
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))

    complete(client, KEY_A)
    complete(client, KEY_A)
    assert complete(client, KEY_A).status_code == 429

    assert complete(client, KEY_B).status_code == 200


@respx.mock
def test_an_unauthenticated_request_is_refused_before_it_is_counted(
    client: TestClient,
) -> None:
    """401 comes first: there is no bucket to charge until we know whose it is."""
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))

    for _ in range(5):
        assert client.post("/v1/complete", json={"prompt": "hi"}).status_code == 401

    assert complete(client, KEY_A).status_code == 200


@respx.mock
def test_the_budget_is_reported_even_when_the_request_fails(
    client: TestClient,
) -> None:
    """The token was spent, so the caller is owed the count regardless."""
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(500, json={}))

    response = complete(client, KEY_A)

    assert response.status_code == 502
    assert response.headers["x-ratelimit-limit"] == "2"
    assert response.headers["x-ratelimit-remaining"] == "1"


@respx.mock
def test_rate_limiting_can_be_turned_off() -> None:
    with build_client(RATE_LIMIT_ENABLED=False) as client:
        respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))

        for _ in range(5):
            assert complete(client, KEY_A).status_code == 200
