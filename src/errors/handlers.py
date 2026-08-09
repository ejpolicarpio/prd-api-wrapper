from typing import cast

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger

from src.errors.exceptions import AppError, ValidationFailed


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    error = cast(AppError, exc)

    logger.warning(
        "{} {} -> {} {}",
        request.method,
        request.url.path,
        error.status_code,
        error.code,
    )

    return JSONResponse(
        status_code=error.status_code,
        content=error.to_payload(),
        headers=error.headers or None,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render FastAPI's own 422s in our envelope, so clients parse one shape."""
    invalid = cast(RequestValidationError, exc)

    failure = ValidationFailed(details={"fields": jsonable_encoder(invalid.errors())})

    return JSONResponse(status_code=failure.status_code, content=failure.to_payload())


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defence: a bug reaches the client as a generic 500.

    The traceback goes to the logs, never into the response -- it can leak
    file paths, dependency versions, and occasionally secrets.
    """
    logger.opt(exception=exc).error(
        "unhandled error on {} {}", request.method, request.url.path
    )

    generic = AppError()

    return JSONResponse(status_code=generic.status_code, content=generic.to_payload())


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
