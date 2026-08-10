import pytest

from src.errors.exceptions import (
    AppError,
    ModelNotFound,
    UpstreamCircuitOpen,
    UpstreamError,
    UpstreamRateLimited,
)
from src.services.resilience import CircuitBreaker, CircuitState, RetryPolicy


class FakeClock:
    """A clock we advance by hand, so tests never actually wait."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def policy(**overrides) -> RetryPolicy:
    defaults = {
        "max_attempts": 3,
        "initial_backoff": 1.0,
        "max_backoff": 8.0,
        "budget": 30.0,
        "jitter": lambda: 1.0,  # deterministic: always the full delay
    }

    return RetryPolicy(**(defaults | overrides))


# Backoff arithmetic
# ========================================================


def test_backoff_grows_exponentially() -> None:
    assert [policy().delay_for(attempt) for attempt in (1, 2, 3)] == [1.0, 2.0, 4.0]


def test_backoff_is_capped() -> None:
    assert policy(max_backoff=3.0).delay_for(5) == 3.0


def test_jitter_keeps_the_delay_within_the_window() -> None:
    """Full jitter picks uniformly from [0, cap] -- never more than the cap."""
    jittered = policy(jitter=lambda: 0.25).delay_for(3)

    assert jittered == 1.0  # 4.0 * 0.25, i.e. inside [0, 4.0]


def test_retry_after_from_the_provider_wins_over_our_guess() -> None:
    assert policy().delay_for(1, retry_after=5.0) == 5.0


def test_retry_after_is_still_capped() -> None:
    """A provider asking us to wait an hour should not pin a worker for one."""
    assert policy(max_backoff=8.0).delay_for(1, retry_after=3600.0) == 8.0


# Retry behaviour
# ========================================================


async def test_succeeds_without_retrying_when_the_call_works() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await policy().run(operation) == "ok"
    assert calls == 1


async def test_retries_a_retryable_failure_then_succeeds() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise UpstreamError()
        return "ok"

    assert await policy(initial_backoff=0.0).run(operation) == "ok"
    assert calls == 3


async def test_does_not_retry_a_client_error() -> None:
    """Repeating a rejected request only spends quota to fail identically."""
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise ModelNotFound()

    with pytest.raises(ModelNotFound):
        await policy(initial_backoff=0.0).run(operation)

    assert calls == 1


async def test_gives_up_after_the_attempt_cap() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise UpstreamError()

    with pytest.raises(UpstreamError):
        await policy(max_attempts=3, initial_backoff=0.0).run(operation)

    assert calls == 3


async def test_stops_when_the_next_wait_would_exceed_the_budget() -> None:
    clock = FakeClock()
    calls = 0

    async def sleep(seconds: float) -> None:
        clock.advance(seconds)

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise UpstreamError()

    retry = policy(
        max_attempts=10,
        initial_backoff=4.0,
        budget=10.0,
        sleep=sleep,
        clock=clock,
    )

    with pytest.raises(UpstreamError):
        await retry.run(operation)

    # waits 4s then 8s = 12s > 10s budget, so the third wait never happens
    assert calls == 2


async def test_honours_retry_after_when_sleeping() -> None:
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UpstreamRateLimited(retry_after=2.5)
        return "ok"

    await policy(sleep=sleep).run(operation)

    assert slept == [2.5]


# Circuit breaker
# ========================================================


def test_circuit_opens_after_consecutive_failures() -> None:
    breaker = CircuitBreaker(failure_threshold=3)

    for _ in range(3):
        breaker.before_call()
        breaker.record_failure()

    assert breaker.state is CircuitState.OPEN

    with pytest.raises(UpstreamCircuitOpen):
        breaker.before_call()


def test_success_resets_the_failure_count() -> None:
    breaker = CircuitBreaker(failure_threshold=3)

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()

    breaker.before_call()  # still closed: the streak was broken
    assert breaker.state is CircuitState.CLOSED


def test_circuit_probes_once_after_the_reset_timeout() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=30.0, clock=clock)

    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN

    clock.advance(29.0)
    with pytest.raises(UpstreamCircuitOpen):
        breaker.before_call()

    clock.advance(2.0)
    breaker.before_call()  # allowed through as a probe
    assert breaker.state is CircuitState.HALF_OPEN

    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


def test_a_failed_probe_reopens_the_circuit_immediately() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=5, reset_timeout=30.0, clock=clock)

    for _ in range(5):
        breaker.record_failure()

    clock.advance(31.0)
    breaker.before_call()
    breaker.record_failure()  # the probe failed

    assert breaker.state is CircuitState.OPEN
    with pytest.raises(UpstreamCircuitOpen):
        breaker.before_call()


def test_non_retryable_failures_are_never_reported_to_the_breaker() -> None:
    """Guard on the contract the service relies on: only infrastructure
    failures say anything about provider health."""
    assert AppError.retryable is False
    assert ModelNotFound.retryable is False
    assert UpstreamCircuitOpen.retryable is False
    assert UpstreamError.retryable is True
    assert UpstreamRateLimited.retryable is True
