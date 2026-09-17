"""Centralised mapping from exceptions to HTTP responses.

Every error the API returns is shaped like :class:`ErrorResponse`, carries a
stable machine-readable ``code``, and never leaks a stack trace or a database
message to the client.
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.schemas import ErrorResponse
from app.core.logging import request_id_ctx
from app.domain.exceptions import (
    DomainError,
    IdempotencyKeyConflictError,
    InsufficientFundsError,
    ServiceUnavailableError,
    WalletAlreadyExistsError,
    WalletNotFoundError,
)

logger = logging.getLogger(__name__)

#: Which domain failure becomes which status code. Adding a domain error without
#: an entry here falls back to 400, never to an unhandled 500.
_STATUS_BY_ERROR: Final[dict[type[DomainError], int]] = {
    WalletNotFoundError: status.HTTP_404_NOT_FOUND,
    InsufficientFundsError: status.HTTP_409_CONFLICT,
    IdempotencyKeyConflictError: status.HTTP_409_CONFLICT,
    WalletAlreadyExistsError: status.HTTP_409_CONFLICT,
    ServiceUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
}

_GENERIC_500 = "Internal server error"


def _render(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
) -> ORJSONResponse:
    payload = ErrorResponse(
        code=code,
        message=message,
        request_id=request_id_ctx.get(),
        details=details,
    )
    return ORJSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


async def domain_error_handler(_: Request, exc: Exception) -> ORJSONResponse:
    assert isinstance(exc, DomainError)  # noqa: S101 - handler is registered for this type
    status_code = _STATUS_BY_ERROR.get(type(exc), status.HTTP_400_BAD_REQUEST)
    if status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        logger.error("domain error", extra={"code": exc.code}, exc_info=exc)
    return _render(status_code, exc.code, exc.message)


async def validation_error_handler(_: Request, exc: Exception) -> ORJSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    details: list[dict[str, object]] = [
        {
            "field": ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return _render(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "validation_error",
        "Request validation failed",
        details,
    )


async def http_exception_handler(_: Request, exc: Exception) -> ORJSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = {
        status.HTTP_404_NOT_FOUND: "not_found",
        status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
        status.HTTP_429_TOO_MANY_REQUESTS: "too_many_requests",
    }.get(exc.status_code, "http_error")
    detail = exc.detail if isinstance(exc.detail, str) else _GENERIC_500
    return _render(exc.status_code, code, detail)


async def database_error_handler(_: Request, exc: Exception) -> ORJSONResponse:
    """Turn an unexpected database failure into a 503, with the detail logged."""
    logger.exception("unhandled database error", exc_info=exc)
    return _render(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "service_unavailable",
        "Service temporarily unavailable, please retry",
    )


async def unhandled_error_handler(_: Request, exc: Exception) -> ORJSONResponse:
    """Last resort. The client learns nothing beyond the correlation id."""
    logger.exception("unhandled application error", exc_info=exc)
    return _render(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        _GENERIC_500,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(SQLAlchemyError, database_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
