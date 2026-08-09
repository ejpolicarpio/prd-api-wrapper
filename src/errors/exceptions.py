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

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.details = details or {}
        self.headers = headers or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details

        return {"error": error}


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


class UpstreamRateLimited(AppError):
    status_code = 429
    code = "upstream_rate_limited"
    message = "Rate limited by the provider. Retry shortly."


# Our problem, not the caller's
# ========================================================


class UpstreamError(AppError):
    status_code = 502
    code = "upstream_error"
    message = "The provider returned an unexpected error."


class UpstreamUnavailable(AppError):
    status_code = 503
    code = "upstream_unavailable"
    message = "The provider could not be reached."


class UpstreamTimeout(AppError):
    status_code = 504
    code = "upstream_timeout"
    message = "The provider did not respond in time."


class UpstreamAuthFailed(AppError):
    # 502, not 401: the credentials at fault are *ours*, so telling the caller
    # to re-authenticate would send them chasing a problem they cannot fix.
    status_code = 502
    code = "upstream_auth_failed"
    message = "The provider rejected our credentials."


class InvalidUpstreamResponse(AppError):
    status_code = 502
    code = "invalid_upstream_response"
    message = "The provider returned a response we could not parse."
