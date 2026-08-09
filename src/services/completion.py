import httpx
from pydantic import ValidationError

from src.configuration import Settings
from src.errors.exceptions import (
    AppError,
    InvalidUpstreamResponse,
    ModelNotFound,
    UpstreamAuthFailed,
    UpstreamError,
    UpstreamRateLimited,
    UpstreamRejectedRequest,
    UpstreamTimeout,
    UpstreamUnavailable,
)
from src.models.completion import (
    CompletionRequest,
    CompletionResponse,
    CompletionUsage,
)


class CompletionService:
    """Owns every detail of talking to the upstream provider.

    Endpoints never see httpx, upstream JSON, or the API key -- they call this
    with our models and get our models back. That boundary is what lets us swap
    providers (or add retries, caching, metering) without touching the routes.
    """

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = request.model or self._settings.UPSTREAM_MODEL

        payload: dict = {
            "model": model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout() from exc
        except httpx.RequestError as exc:
            # DNS failure, refused connection, TLS problem: never reached them.
            raise UpstreamUnavailable() from exc

        if response.is_error:
            raise self._as_app_error(response, model)

        try:
            body = response.json()
        except ValueError as exc:
            raise InvalidUpstreamResponse() from exc

        return self._to_response(body)

    # Error mapping
    # ========================================================

    @classmethod
    def _as_app_error(cls, response: httpx.Response, model: str) -> AppError:
        status = response.status_code
        upstream_message = cls._upstream_message(response)
        details = {"upstream_status": status}

        if status == 404:
            return ModelNotFound(
                upstream_message or f"Model {model!r} is not available.",
                details={"model": model},
            )

        if status == 429:
            retry_after = response.headers.get("retry-after")
            return UpstreamRateLimited(
                headers={"Retry-After": retry_after} if retry_after else None,
                details=details,
            )

        if status in (401, 403):
            # Deliberately drop the upstream message: it concerns our key.
            return UpstreamAuthFailed(details=details)

        if 400 <= status < 500:
            return UpstreamRejectedRequest(upstream_message, details=details)

        return UpstreamError(details=details)

    @staticmethod
    def _upstream_message(response: httpx.Response) -> str | None:
        """Pull a human message out of an upstream error body, if there is one."""
        try:
            body = response.json()
        except ValueError:
            return None

        if not isinstance(body, dict):
            return None

        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            return message if isinstance(message, str) else None

        return error if isinstance(error, str) else None

    # Response mapping
    # ========================================================

    @staticmethod
    def _to_response(data: object) -> CompletionResponse:
        # Never trust the shape of someone else's JSON: a KeyError escaping here
        # would surface to our client as a meaningless 500.
        if not isinstance(data, dict):
            raise InvalidUpstreamResponse()

        try:
            usage = data.get("usage") or {}

            return CompletionResponse(
                id=data["id"],
                model=data["model"],
                content=data["choices"][0]["message"]["content"],
                usage=CompletionUsage(
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                ),
            )
        except (
            AttributeError,
            IndexError,
            KeyError,
            TypeError,
            ValidationError,
        ) as exc:
            raise InvalidUpstreamResponse() from exc
