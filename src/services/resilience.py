import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum

from loguru import logger

from src.errors.exceptions import AppError, UpstreamCircuitOpen


class RetryPolicy:
    """Retries an operation that failed in a way worth repeating.

    Two rules do most of the work here. Only failures the taxonomy marks
    `retryable` are repeated -- retrying a rejected request just spends quota
    to fail identically. And every wait is randomised, because synchronised
    retries from many clients are how a recovering provider gets knocked back
    over (the "thundering herd").
    """

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        initial_backoff: float = 0.5,
        max_backoff: float = 8.0,
        budget: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_attempts = max_attempts
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._budget = budget
        self._sleep = sleep
        self._jitter = jitter
        self._clock = clock

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Seconds to wait *after* the given 1-based attempt failed.

        When the provider told us how long to wait, believe it. Otherwise back
        off exponentially and apply full jitter: a uniform pick from [0, cap]
        rather than the cap itself.
        """
        if retry_after is not None:
            return min(retry_after, self._max_backoff)

        exponential = self._initial_backoff * (2 ** (attempt - 1))

        return min(exponential, self._max_backoff) * self._jitter()

    async def run[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        started = self._clock()
        attempt = 0

        while True:
            attempt += 1
            try:
                return await operation()
            except AppError as error:
                if not error.retryable or attempt >= self._max_attempts:
                    raise

                delay = self.delay_for(attempt, error.retry_after)

                # A retry that would blow the budget is worse than the failure:
                # it holds a connection open to arrive at the same answer later.
                if self._clock() - started + delay > self._budget:
                    raise

                logger.warning(
                    "attempt {} failed with {}; retrying in {:.2f}s",
                    attempt,
                    error.code,
                    delay,
                )
                await self._sleep(delay)


class CircuitState(StrEnum):
    CLOSED = "closed"  # everything through
    OPEN = "open"  # provider is down; fail immediately
    HALF_OPEN = "half_open"  # let a trial request decide


class CircuitBreaker:
    """Stops calling a provider that is consistently failing.

    Retrying helps with a blip. It actively hurts during a real outage: every
    caller queues behind a provider that cannot answer, and their downtime
    becomes ours. After enough consecutive failures the circuit opens and
    requests fail instantly, until a single trial request finds it healthy.

    State is per process, so with several workers each holds its own view.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._clock = clock

        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0

    @property
    def state(self) -> CircuitState:
        return self._state

    def before_call(self) -> None:
        """Raise UpstreamCircuitOpen if we should not be calling right now."""
        if self._state is not CircuitState.OPEN:
            return

        if self._clock() - self._opened_at < self._reset_timeout:
            raise UpstreamCircuitOpen()

        # Cooled off: let exactly one request through to test the water.
        # (Nothing here caps concurrent trials -- a real breaker would.)
        self._state = CircuitState.HALF_OPEN
        logger.info("circuit half-open: probing the provider")

    def record_success(self) -> None:
        if self._state is not CircuitState.CLOSED:
            logger.info("circuit closed: provider healthy again")

        self._state = CircuitState.CLOSED
        self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1

        if (
            self._state is CircuitState.HALF_OPEN
            or self._failures >= self._failure_threshold
        ):
            self._trip()

    def _trip(self) -> None:
        if self._state is not CircuitState.OPEN:
            logger.warning(
                "circuit open after {} failures; pausing calls for {}s",
                self._failures,
                self._reset_timeout,
            )

        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
