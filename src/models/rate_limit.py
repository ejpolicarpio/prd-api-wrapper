from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    """The outcome of charging a caller's bucket for one request."""

    allowed: bool
    limit: int
    remaining: int
    reset_after: float  # seconds until the bucket is full again
    retry_after: float  # seconds until one token is available; 0 when allowed
