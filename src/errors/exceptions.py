from typing import Any


class AppError(Exception):
    """Base for every failure this service returns on purpose.

    Each subclass pins two things: the HTTP status we answer with, and a
    stable machine-readable `code`. Clients branch on the code; the message
    is for humans and may change. Anything raised that is *not* an AppError
    is a bug, and is reported as a generic 500.
    """

    status_code: int = 500
    code: str = "internal_error"
    message: str = "Something went wrong on our side."

    #: Whether repeating the identical request could plausibly succeed.
    #: This is what drives the retry policy -- see services/resilience.py.
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.details = details or {}
        self.headers = headers or {}
        self.retry_after = retry_after
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details

        return {"error": error}


# The caller is not who we serve
# ========================================================


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"
    message = "Authentication is required."

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        # RFC 9110: a 401 must say how to authenticate.
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class MissingCredentials(Unauthenticated):
    code = "missing_credentials"
    message = "Provide an API key as 'Authorization: Bearer <key>'."


class InvalidCredentials(Unauthenticated):
    code = "invalid_credentials"
    message = "The provided API key is not valid."


# The caller made a mistake
# ========================================================


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_error"
    message = "The request body is invalid."


class ModelNotFound(AppError):
    status_code = 400
    code = "model_not_found"
    message = "The requested model is not available."


class UpstreamRejectedRequest(AppError):
    status_code = 400
    code = "upstream_rejected_request"
    message = "The provider rejected the request."


class RateLimitExceeded(AppError):
    # Ours, not the provider's: the caller has spent their own allowance.
    status_code = 429
    code = "rate_limit_exceeded"
    message = "Rate limit exceeded. Slow down."


class UpstreamRateLimited(AppError):
    status_code = 429
    code = "upstream_rate_limited"
    message = "Rate limited by the provider. Retry shortly."
    retryable = True


# Our problem, not the caller's
# ========================================================


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"
    message = "The provider returned an unexpected error."
    retryable = True


class UpstreamUnavailable(AppError):
    status_code = 503
    code = "upstream_unavailable"
    message = "The provider could not be reached."
    retryable = True


class UpstreamTimeout(AppError):
    status_code = 504
    code = "upstream_timeout"
    message = "The provider did not respond in time."
    retryable = True


class UpstreamCircuitOpen(AppError):
    # Not retryable: the breaker exists precisely to stop us trying.
    status_code = 503
    code = "upstream_circuit_open"
    message = "The provider is failing; calls are paused. Retry shortly."


class UpstreamAuthFailed(AppError):
    # 502, not 401: the credentials at fault are *ours*, so telling the caller
    # to re-authenticate would send them chasing a problem they cannot fix.
    status_code = 502
    code = "upstream_auth_failed"
    message = "The provider rejected our credentials."


class WebhookDeliveryFailed(AppError):
    """Never reaches a client: by the time we deliver, their request is long
    finished. It exists so the retry policy can classify delivery attempts."""

    status_code = 502
    code = "webhook_delivery_failed"
    message = "The callback could not be delivered."

    def __init__(self, message: str | None = None, *, retryable: bool = True, **kwargs):
        super().__init__(message, **kwargs)
        # Per-instance, unlike the others: whether a delivery is worth
        # repeating depends on the receiver's status code, not on the class.
        self.retryable = retryable


class JobNotFound(AppError):
    status_code = 404
    code = "job_not_found"
    message = "No such job."


class InvalidUpstreamResponse(AppError):
    status_code = 502
    code = "invalid_upstream_response"
    message = "The provider returned a response we could not parse."
