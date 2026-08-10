import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from src.models.caller import ApiKeyRecord, hash_api_key
from src.services.webhooks import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign, verify
from tests.support import OK_BODY, UPSTREAM_ROUTE, build_test_client

KEY_A = "sk-client-a"
KEY_B = "sk-client-b"
SECRET = "whsec-test"
CALLBACK = "https://client.test/hooks"

KEYS = [
    ApiKeyRecord(id="a", name="Client A", key_hash=hash_api_key(KEY_A)),
    ApiKeyRecord(id="b", name="Client B", key_hash=hash_api_key(KEY_B)),
]


def build_client(**overrides) -> TestClient:
    return build_test_client(
        RETRY_MAX_ATTEMPTS=1,
        RETRY_INITIAL_BACKOFF_SECONDS=0.0,
        WEBHOOK_MAX_ATTEMPTS=3,
        WEBHOOK_SIGNING_SECRET=SECRET,
        api_keys=KEYS,
        **overrides,
    )


@pytest.fixture
def client():
    with build_client() as test_client:
        yield test_client


def submit(client: TestClient, key: str = KEY_A, **body) -> httpx.Response:
    payload = {"prompt": "hi", "callback_url": CALLBACK} | body

    return client.post(
        "/v1/jobs", json=payload, headers={"Authorization": f"Bearer {key}"}
    )


def fetch(client: TestClient, job_id: str, key: str = KEY_A) -> httpx.Response:
    return client.get(f"/v1/jobs/{job_id}", headers={"Authorization": f"Bearer {key}"})


# Accepting work
# ========================================================


@respx.mock
def test_a_job_is_accepted_immediately(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    respx.post(CALLBACK).mock(return_value=httpx.Response(200))

    response = submit(client)

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert response.json()["id"]


def test_a_job_needs_a_key(client: TestClient) -> None:
    response = client.post("/v1/jobs", json={"prompt": "hi", "callback_url": CALLBACK})

    assert response.status_code == 401


def test_the_callback_url_must_be_http(client: TestClient) -> None:
    """Rejected at the edge, so we never hold a job we cannot deliver."""
    response = submit(client, callback_url="file:///etc/passwd")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# Running and delivering
# ========================================================


@respx.mock
def test_the_result_is_delivered_to_the_callback(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    callback = respx.post(CALLBACK).mock(return_value=httpx.Response(200))

    job_id = submit(client).json()["id"]

    assert callback.called
    body = json.loads(callback.calls.last.request.content)
    assert body["job_id"] == job_id
    assert body["status"] == "succeeded"
    assert body["content"] == "hello"


@respx.mock
def test_the_delivery_is_signed(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    callback = respx.post(CALLBACK).mock(return_value=httpx.Response(200))

    submit(client)

    request = callback.calls.last.request
    signature = request.headers[SIGNATURE_HEADER]
    timestamp = request.headers[TIMESTAMP_HEADER]

    assert signature.startswith("sha256=")
    assert verify(SECRET, timestamp, request.content, signature)


@respx.mock
def test_a_signature_made_with_the_wrong_secret_does_not_verify(
    client: TestClient,
) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    callback = respx.post(CALLBACK).mock(return_value=httpx.Response(200))

    submit(client)

    request = callback.calls.last.request

    assert not verify(
        "whsec-wrong",
        request.headers[TIMESTAMP_HEADER],
        request.content,
        request.headers[SIGNATURE_HEADER],
    )


@respx.mock
def test_replaying_a_payload_under_a_new_timestamp_breaks_the_signature(
    client: TestClient,
) -> None:
    """The timestamp is inside the signed material, not merely alongside it."""
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    callback = respx.post(CALLBACK).mock(return_value=httpx.Response(200))

    submit(client)
    request = callback.calls.last.request

    assert not verify(
        SECRET, "9999999999", request.content, request.headers[SIGNATURE_HEADER]
    )


@respx.mock
def test_a_tampered_body_does_not_verify(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    callback = respx.post(CALLBACK).mock(return_value=httpx.Response(200))

    submit(client)
    request = callback.calls.last.request

    assert not verify(
        SECRET,
        request.headers[TIMESTAMP_HEADER],
        request.content + b" ",
        request.headers[SIGNATURE_HEADER],
    )


@respx.mock
def test_the_provider_key_is_never_sent_to_the_callback(client: TestClient) -> None:
    """The callback URL is chosen by the caller; the upstream client's
    Authorization header must not follow it there."""
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    callback = respx.post(CALLBACK).mock(return_value=httpx.Response(200))

    submit(client)

    assert "authorization" not in callback.calls.last.request.headers


# Failure paths
# ========================================================


@respx.mock
def test_a_failed_job_still_notifies_the_caller(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(
        return_value=httpx.Response(404, json={"error": {"message": "no model"}})
    )
    callback = respx.post(CALLBACK).mock(return_value=httpx.Response(200))

    job_id = submit(client, model="nope").json()["id"]

    body = json.loads(callback.calls.last.request.content)
    assert body["status"] == "failed"
    assert body["error_code"] == "model_not_found"
    assert body["content"] is None

    assert fetch(client, job_id).json()["status"] == "failed"


@respx.mock
def test_delivery_is_retried_when_the_receiver_is_down(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    callback = respx.post(CALLBACK).mock(
        side_effect=[httpx.Response(503), httpx.Response(200)]
    )

    job_id = submit(client).json()["id"]

    assert callback.call_count == 2
    assert fetch(client, job_id).json()["delivery_attempts"] == 2


@respx.mock
def test_delivery_is_not_retried_when_the_receiver_rejects_it(
    client: TestClient,
) -> None:
    """A 400 from a receiver is their bug, not a blip; repeating it is waste."""
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    callback = respx.post(CALLBACK).mock(return_value=httpx.Response(400))

    submit(client)

    assert callback.call_count == 1


@respx.mock
def test_an_undeliverable_result_is_still_recorded(client: TestClient) -> None:
    """Delivery failing does not lose the work -- the caller can still poll."""
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    respx.post(CALLBACK).mock(return_value=httpx.Response(500))

    job_id = submit(client).json()["id"]

    job = fetch(client, job_id).json()
    assert job["status"] == "succeeded"
    assert job["content"] == "hello"
    assert job["delivered_at"] is None


# Reading jobs back
# ========================================================


@respx.mock
def test_a_job_can_be_polled(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    respx.post(CALLBACK).mock(return_value=httpx.Response(200))

    job_id = submit(client).json()["id"]
    job = fetch(client, job_id).json()

    assert job["status"] == "succeeded"
    assert job["content"] == "hello"
    assert job["delivered_at"] is not None


@respx.mock
def test_a_job_is_invisible_to_other_callers(client: TestClient) -> None:
    """Otherwise a job id on its own would be enough to read someone's output."""
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(200, json=OK_BODY))
    respx.post(CALLBACK).mock(return_value=httpx.Response(200))

    job_id = submit(client, key=KEY_A).json()["id"]

    response = fetch(client, job_id, key=KEY_B)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_an_unknown_job_is_a_404(client: TestClient) -> None:
    assert fetch(client, "does-not-exist").status_code == 404


# Signing helper
# ========================================================


def test_signing_is_stable_and_secret_dependent() -> None:
    body = b'{"job_id":"1"}'

    assert sign("s", 100, body) == sign("s", 100, body)
    assert sign("s", 100, body) != sign("other", 100, body)
    assert sign("s", 100, body) != sign("s", 101, body)
