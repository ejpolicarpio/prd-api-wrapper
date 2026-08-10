import math
import time
from collections.abc import Callable
from typing import Protocol

from src.models.rate_limit import RateLimitDecision


class TokenBucket:
    """Capacity `capacity`, refilled continuously at `refill_rate` per second.

    Tokens accumulate up to the cap, so a caller who has been quiet may spend a
    burst at once, while a caller hammering the endpoint settles into the
    steady rate. That is the property a fixed window lacks: with a per-minute
    counter, a client can fire the whole allowance at 11:59:59 and again at
    12:00:00, briefly doubling the rate you thought you enforced.
    """

    def __init__(
        self,
        capacity: int,
        refill_rate: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._clock = clock

        self._tokens = float(capacity)
        self._updated_at = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated_at

        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
        self._updated_at = now

    def take(self, tokens: float = 1.0) -> RateLimitDecision:
        self._refill()

        allowed = self._tokens >= tokens
        if allowed:
            self._tokens -= tokens

        missing = max(0.0, tokens - self._tokens)

        return RateLimitDecision(
            allowed=allowed,
            limit=self._capacity,
            remaining=math.floor(self._tokens),
            reset_after=(self._capacity - self._tokens) / self._refill_rate,
            retry_after=0.0 if allowed else missing / self._refill_rate,
        )


class RateLimiter(Protocol):
    async def check(self, key: str) -> RateLimitDecision: ...


class InMemoryRateLimiter:
    """One bucket per caller, held in this process.

    Two limitations worth knowing rather than discovering. Buckets live in
    local memory, so N uvicorn workers enforce N times the intended limit --
    the fix is a shared store like Redis, which is why `check` is async here
    despite doing no I/O. And the dict grows with every distinct caller seen,
    with nothing evicting it; a shared store with TTLs solves that too.
    """

    def __init__(
        self,
        capacity: int,
        refill_rate: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    async def check(self, key: str) -> RateLimitDecision:
        bucket = self._buckets.get(key)

        if bucket is None:
            bucket = TokenBucket(self._capacity, self._refill_rate, self._clock)
            self._buckets[key] = bucket

        # No await between read and write, so on a single event loop this is
        # atomic without a lock.
        return bucket.take()
