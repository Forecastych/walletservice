"""HTTP middleware."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_ctx

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_CLIENT_REQUEST_ID_LEN = 128


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign every request a correlation id and log its outcome.

    A client-supplied ``X-Request-ID`` is honoured so a trace can span services,
    but it is length-capped and sanitised: it ends up in log output, and
    unbounded client input does not belong there.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = self._resolve_request_id(request)
        token = request_id_ctx.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise
        finally:
            request_id_ctx.reset(token)

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "request handled",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response

    @staticmethod
    def _resolve_request_id(request: Request) -> str:
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        if incoming and len(incoming) <= _MAX_CLIENT_REQUEST_ID_LEN:
            # Keep only characters that are safe in logs and headers.
            cleaned = "".join(c for c in incoming if c.isalnum() or c in "-_.:")
            if cleaned:
                return cleaned
        return str(uuid.uuid4())
