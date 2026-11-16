"""Error envelope: every non-2xx response is {"error": {"code", "message", "details"?}}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("promptdox.errors")


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"error": error}


# Constructors for the codes in API_CONTRACT.md section 2.
def validation_error(message: str, fields: dict[str, str] | None = None) -> AppError:
    return AppError(400, "validation_error", message, {"fields": fields} if fields else None)


def unauthorized(message: str = "Please sign in to continue.") -> AppError:
    return AppError(401, "unauthorized", message)


def document_not_found() -> AppError:
    return AppError(404, "document_not_found", "We couldn't find that document.")


def document_not_ready() -> AppError:
    return AppError(409, "document_not_ready", "This document is still being indexed. Try again in a moment.")


def demo_restricted(message: str) -> AppError:
    return AppError(403, "demo_restricted", message)


_STATUS_CODES = {
    400: ("validation_error", "The request is invalid."),
    401: ("unauthorized", "Please sign in to continue."),
    403: ("forbidden", "You don't have access to this."),
    404: ("not_found", "That resource doesn't exist."),
    405: ("method_not_allowed", "That method isn't allowed here."),
    413: ("file_too_large", "The file is larger than 50 MB."),
    429: ("rate_limited", "Too many requests. Please slow down."),
}


def _field_name(location: tuple[Any, ...]) -> str:
    parts = [str(part) for part in location if part not in ("body", "query", "path", "header")]
    return ".".join(parts) or "body"


def _clean_message(message: str) -> str:
    return message.removeprefix("Value error, ")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields: dict[str, str] = {}
        for error in exc.errors():
            field = _field_name(tuple(error.get("loc", ())))
            fields.setdefault(field, _clean_message(error.get("msg", "Invalid value")))
        first = next(iter(fields.values()), "The request is invalid.")
        message = first if len(fields) == 1 else "Some fields are invalid."
        return JSONResponse(status_code=400, content=error_body("validation_error", message, {"fields": fields}))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, default_message = _STATUS_CODES.get(exc.status_code, ("error", "Something went wrong."))
        custom = isinstance(exc.detail, str) and exc.status_code not in (404, 405)
        message = exc.detail if custom else default_message
        return JSONResponse(status_code=exc.status_code, content=error_body(code, message), headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "Something went wrong on our side. Please try again."),
        )
