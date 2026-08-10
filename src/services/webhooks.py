import hashlib
import hmac
import time

import httpx
from loguru import logger

from src.errors.exceptions import WebhookDeliveryFailed
from src.models.job import WebhookPayload
from src.services.resilience import RetryPolicy

SIGNATURE_HEADER = "X-Signature"
TIMESTAMP_HEADER = "X-Signature-Timestamp"


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """HMAC-SHA256 over `timestamp.body`.

    The timestamp is inside the signed material, not merely alongside it: a
    receiver that also rejects old timestamps then cannot be fooled by
    replaying a captured payload, because changing the timestamp invalidates
    the signature.

    The body is signed as raw bytes, so the receiver must verify before
    parsing -- re-serialising JSON can reorder keys and break the digest.
    """
    message = f"{timestamp}.".encode() + body

    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

    return f"sha256={digest}"


def verify(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Provided for tests and for documenting what a receiver should do."""
    expected = sign(secret, int(timestamp), body)

    # compare_digest, not ==: a plain comparison returns early on the first
    # differing byte, which leaks how much of a forged signature was right.
    return hmac.compare_digest(expected, signature)


class WebhookService:
    def __init__(
        self,
        client: httpx.AsyncClient,
        retry: RetryPolicy,
        secret: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client
        self._retry = retry
        self._secret = secret
        self._timeout = timeout_seconds

    async def deliver(self, url: str, payload: WebhookPayload) -> int:
        """POST the payload, retrying transient failures. Returns attempts made."""
        body = payload.model_dump_json().encode()
        attempts = 0

        async def attempt() -> None:
            nonlocal attempts
            attempts += 1
            await self._post(url, body)

        try:
            await self._retry.run(attempt)
            logger.info("webhook delivered for job {}", payload.job_id)
        except WebhookDeliveryFailed as error:
            logger.error(
                "webhook undelivered for job {} after {} attempts",
                payload.job_id,
                attempts,
            )
            # So the caller can record how hard we tried, not just that we did.
            error.details["attempts"] = attempts
            raise

        return attempts

    async def _post(self, url: str, body: bytes) -> None:
        timestamp = int(time.time())

        try:
            response = await self._client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    TIMESTAMP_HEADER: str(timestamp),
                    SIGNATURE_HEADER: sign(self._secret, timestamp, body),
                },
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise WebhookDeliveryFailed("Could not reach the callback URL.") from exc

        if response.is_error:
            # 4xx from a receiver is their bug, not a blip, so repeating it is
            # pointless -- except 429, where they are asking us to slow down.
            retryable = response.status_code >= 500 or response.status_code == 429

            raise WebhookDeliveryFailed(
                f"Callback returned {response.status_code}.",
                retryable=retryable,
                details={"callback_status": response.status_code},
            )
