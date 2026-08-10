import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from src.models.caller import ApiKeyRecord, hash_api_key
from src.services.authentication import mint_key
from tests.support import OK_BODY, UPSTREAM_ROUTE, build_test_client

VALID_KEY = "sk-test-key"
OTHER_KEY = "sk-not-registered"

REGISTERED = ApiKeyRecord(id="k1", name="Test client", key_hash=hash_api_key(VALID_KEY))


def build_client(**overrides) -> TestClient:
    overrides.setdefault("api_keys", [REGISTERED])

    return build_test_client(RETRY_MAX_ATTEMPTS=1, **overrides)


@pytest.fixture
def client():
    with build_client() as test_client:
        yield test_client


def complete(client: TestClient, key: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    return client.post("/v1/complete", json={"prompt": "hi"}, headers=headers)


@respx.mock
def test_a_valid_key_is_let_through(client: TestClient) -> None:
    route = respx.post(UPSTREAM_ROUTE).mock(
        return_value=httpx.Response(200, json=OK_BODY)
    )

    assert complete(client, VALID_KEY).status_code == 200
    assert route.called


@respx.mock
def test_no_key_is_refused_before_the_provider_is_touched(client: TestClient) -> None:
    route = respx.post(UPSTREAM_ROUTE).mock(
        return_value=httpx.Response(200, json=OK_BODY)
    )

    response = complete(client)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_credentials"
    assert response.headers["www-authenticate"] == "Bearer"
    assert not route.called  # no quota spent on an anonymous caller


@respx.mock
def test_an_unknown_key_is_refused(client: TestClient) -> None:
    route = respx.post(UPSTREAM_ROUTE).mock(
        return_value=httpx.Response(200, json=OK_BODY)
    )

    response = complete(client, OTHER_KEY)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert not route.called


def test_an_unknown_key_looks_the_same_as_a_malformed_one(client: TestClient) -> None:
    """Distinguishing them would help someone probing for valid keys."""
    unknown = complete(client, OTHER_KEY).json()["error"]
    malformed = complete(client, "not-even-a-key-shape").json()["error"]

    # request_id differs by construction and says nothing about the key, so it
    # is the one field allowed to vary between the two.
    assert unknown.pop("request_id") != malformed.pop("request_id")
    assert unknown == malformed


def test_the_wrong_scheme_is_not_accepted(client: TestClient) -> None:
    response = client.post(
        "/v1/complete",
        json={"prompt": "hi"},
        headers={"Authorization": "Basic c2VjcmV0"},
    )

    assert response.status_code == 401


def test_health_never_requires_a_key(client: TestClient) -> None:
    """Liveness probes have no credentials to offer."""
    assert client.get("/health").status_code == 200


@respx.mock
def test_auth_can_be_disabled_for_local_work() -> None:
    with build_client(REQUIRE_API_KEY=False) as client:
        respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))

        assert complete(client).status_code == 200


@respx.mock
def test_a_key_is_still_checked_when_auth_is_optional() -> None:
    """Opting out of requiring a key does not mean accepting a wrong one."""
    with build_client(REQUIRE_API_KEY=False) as client:
        respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))

        assert complete(client, OTHER_KEY).status_code == 401


def test_fails_closed_when_no_keys_are_configured() -> None:
    with build_client(api_keys=[]) as client:
        assert complete(client, VALID_KEY).status_code == 401


# Minting
# ========================================================


def test_minted_keys_are_unique_and_stored_only_as_a_digest() -> None:
    first_key, first_record = mint_key("Client A")
    second_key, _ = mint_key("Client A")

    assert first_key != second_key
    assert first_key not in first_record.model_dump_json()
    assert first_record.key_hash == hash_api_key(first_key)
    assert first_record.name == "Client A"


@respx.mock
def test_a_minted_key_actually_works() -> None:
    key, record = mint_key("Fresh client")

    with build_client(api_keys=[record]) as client:
        respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))

        assert complete(client, key).status_code == 200
