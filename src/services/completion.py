import httpx

from src.configuration import Settings
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

        response = await self._client.post("/chat/completions", json=payload)
        # TODO(phase 3): map upstream failures onto our own error taxonomy.
        response.raise_for_status()

        return self._to_response(response.json())

    @staticmethod
    def _to_response(data: dict) -> CompletionResponse:
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
