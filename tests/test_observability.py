import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from loguru import logger

from src.configuration import Settings
from src.dependencies.common import get_session
from src.factory import check_production_settings, create_app
from src.middleware.request_id import REQUEST_ID_HEADER
from tests.support import UPSTREAM_BASE_URL, UPSTREAM_ROUTE, build_test_client

MODELS_ROUTE = f"{UPSTREAM_BASE_URL}/models"


@pytest.fixture
def client():
    with build_test_client(REQUIRE_API_KEY=False, RETRY_MAX_ATTEMPTS=1) as test_client:
        yield test_client


@pytest.fixture
def captured_logs():
    """Collect log records instead of printing them."""
    records: list[dict] = []
    sink_id = logger.add(lambda message: records.append(message.record), level="DEBUG")

    yield records

    logger.remove(sink_id)


# Request ids
# ========================================================


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert response.headers[REQUEST_ID_HEADER]


def test_ids_differ_between_requests(client: TestClient) -> None:
    first = client.get("/health").headers[REQUEST_ID_HEADER]
    second = client.get("/health").headers[REQUEST_ID_HEADER]

    assert first != second


def test_a_caller_supplied_id_is_honoured(client: TestClient) -> None:
    """So a trace can span the caller's system and ours."""
    response = client.get("/health", headers={REQUEST_ID_HEADER: "caller-trace-1"})

    assert response.headers[REQUEST_ID_HEADER] == "caller-trace-1"


def test_an_unsafe_inbound_id_is_replaced(client: TestClient) -> None:
    """An arbitrary string would be echoed into headers and log lines."""
    response = client.get("/health", headers={REQUEST_ID_HEADER: "bad\nid: injected"})

    assert response.headers[REQUEST_ID_HEADER] != "bad\nid: injected"


def test_a_route_that_does_not_exist_still_gets_an_id(client: TestClient) -> None:
    """404s are exactly the requests someone later asks you to explain."""
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.headers[REQUEST_ID_HEADER]


@respx.mock
def test_errors_report_the_request_id_to_the_caller(client: TestClient) -> None:
    respx.post(UPSTREAM_ROUTE).mock(return_value=httpx.Response(500, json={}))

    response = client.post("/v1/complete", json={"prompt": "hi"})

    assert response.json()["error"]["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_validation_errors_report_it_too(client: TestClient) -> None:
    response = client.post("/v1/complete", json={"prompt": ""})

    assert response.json()["error"]["request_id"] == response.headers[REQUEST_ID_HEADER]


# Logging
# ========================================================


def test_every_request_is_logged_with_its_id(
    client: TestClient, captured_logs: list[dict]
) -> None:
    request_id = client.get("/health").headers[REQUEST_ID_HEADER]

    summaries = [r for r in captured_logs if "-> 200" in r["message"]]

    assert summaries
    assert summaries[-1]["extra"]["request_id"] == request_id


@respx.mock
def test_background_work_logs_under_the_same_id(
    client: TestClient, captured_logs: list[dict]
) -> None:
    """The point of the whole exercise: work that happens after the response
    still traces back to the request that asked for it."""
    respx.post(UPSTREAM_ROUTE).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c1",
                "model": "test-model",
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )
    callback = respx.post("https://client.test/hooks").mock(
        return_value=httpx.Response(200)
    )

    response = client.post(
        "/v1/jobs",
        json={"prompt": "hi", "callback_url": "https://client.test/hooks"},
    )
    request_id = response.headers[REQUEST_ID_HEADER]
    job_id = response.json()["id"]

    assert callback.called

    delivery_logs = [r for r in captured_logs if r["extra"].get("job_id") == job_id]

    assert delivery_logs
    assert all(r["extra"]["request_id"] == request_id for r in delivery_logs)


# Readiness
# ========================================================


class StubSession:
    def __init__(self, healthy: bool = True) -> None:
        self._healthy = healthy

    async def execute(self, *args, **kwargs):
        if not self._healthy:
            raise RuntimeError("database is down")

        return None


def readiness_client(*, database: bool = True) -> TestClient:
    test_client = build_test_client(REQUIRE_API_KEY=False)

    async def session():
        yield StubSession(healthy=database)

    test_client.app.dependency_overrides[get_session] = session

    return test_client


@respx.mock
def test_ready_when_both_dependencies_answer() -> None:
    respx.get(MODELS_ROUTE).mock(return_value=httpx.Response(200, json={"data": []}))

    with readiness_client() as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": True, "upstream": True},
    }


@respx.mock
def test_not_ready_when_the_database_is_down() -> None:
    respx.get(MODELS_ROUTE).mock(return_value=httpx.Response(200, json={"data": []}))

    with readiness_client(database=False) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] is False


@respx.mock
def test_not_ready_when_the_upstream_is_unreachable() -> None:
    respx.get(MODELS_ROUTE).mock(side_effect=httpx.ConnectError("refused"))

    with readiness_client() as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["upstream"] is False


@respx.mock
def test_liveness_stays_up_when_dependencies_are_down() -> None:
    """Restarting the process does not fix a database, so liveness must not
    fail with one -- that turns a partial outage into a total one."""
    respx.get(MODELS_ROUTE).mock(side_effect=httpx.ConnectError("refused"))

    with readiness_client(database=False) as client:
        assert client.get("/health").status_code == 200


# Startup guardrails
# ========================================================


def test_production_refuses_to_start_without_a_signing_secret() -> None:
    with pytest.raises(ValueError, match="WEBHOOK_SIGNING_SECRET"):
        check_production_settings(
            Settings(ENVIRONMENT="production", WEBHOOK_SIGNING_SECRET="")
        )


def test_production_starts_with_one() -> None:
    check_production_settings(
        Settings(ENVIRONMENT="production", WEBHOOK_SIGNING_SECRET="whsec-x")
    )


def test_local_development_needs_no_secret() -> None:
    check_production_settings(Settings(ENVIRONMENT="local", WEBHOOK_SIGNING_SECRET=""))


def test_create_app_enforces_it() -> None:
    with pytest.raises(ValueError):
        create_app(Settings(ENVIRONMENT="production", WEBHOOK_SIGNING_SECRET=""))
