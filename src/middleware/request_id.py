import re
import time
from uuid import uuid4

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

# Inbound ids are echoed into logs and headers, so they are constrained rather
# than trusted: an arbitrary string could forge log lines or inject headers.
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Gives every request an id, and every log line it produces that id.

    Middleware rather than a dependency because this must cover requests that
    never reach a route -- 404s, malformed bodies, unhandled crashes -- which
    are exactly the ones someone will later ask you to explain.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = self._incoming_id(request) or uuid4().hex
        request.state.request_id = request_id

        started = time.perf_counter()

        # contextualize binds for everything logged inside this block, so no
        # call site has to remember to pass the id along.
        with logger.contextualize(request_id=request_id):
            response = await call_next(request)

            duration_ms = round((time.perf_counter() - started) * 1000, 2)

            logger.info(
                "{} {} -> {} in {}ms",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )

            response.headers[REQUEST_ID_HEADER] = request_id

            return response

    @staticmethod
    def _incoming_id(request: Request) -> str | None:
        """Honour a caller's id so a trace can span both sides of the call."""
        candidate = request.headers.get(REQUEST_ID_HEADER)

        return candidate if candidate and SAFE_REQUEST_ID.match(candidate) else None
