import math
from typing import Annotated

from fastapi import Depends, Request, Response

from src.dependencies.auth import CallerDep
from src.dependencies.common import SettingsDep
from src.errors.exceptions import RateLimitExceeded
from src.models.rate_limit import RateLimitDecision
from src.services.rate_limiter import RateLimiter


def get_rate_limiter(request: Request) -> RateLimiter:
    """Shared across requests: per-request buckets would always be full."""
    return request.app.rate_limiter


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def _headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(math.ceil(decision.reset_after)),
    }


async def enforce_rate_limit(
    request: Request,
    caller: CallerDep,
    response: Response,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> None:
    """Spend one token for this caller, or refuse the request.

    Depends on CallerDep, which is what orders it after authentication: there
    is no bucket to charge until we know whose it is.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return

    decision = await limiter.check(caller.id)

    if not decision.allowed:
        raise RateLimitExceeded(
            headers=_headers(decision)
            | {"Retry-After": str(math.ceil(decision.retry_after))},
            details={"limit": decision.limit, "window": "minute"},
        )

    # Tell every caller where they stand, not only the ones we turned away.
    # Both places are needed: `response` covers a successful return, while the
    # stashed copy survives an exception, which discards that response object.
    # The token was spent either way, so the caller is owed the count.
    response.headers.update(_headers(decision))
    request.state.response_headers = _headers(decision)


RateLimitDep = Depends(enforce_rate_limit)
