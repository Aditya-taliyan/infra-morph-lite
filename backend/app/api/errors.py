"""Map every failure to {"error": {"code", "message", "details"}}.

Domain exceptions are translated here, so route handlers stay free of
try/except. Unexpected exceptions are logged with their stack frames (no
message: it can quote user input) and answered with a generic 500; the traceback never reaches the client.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import record_request, safe_traceback
from app.models.errors import ErrorBody, ErrorResponse
from app.services.approval import AlreadyApprovedError, RunNotApprovableError, RunNotFoundError
from app.services.generation import PipelineBusyError

logger = logging.getLogger(__name__)

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_response(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    record_request(error_type=code)
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def field_path(loc: tuple | list) -> str:
    """("body", "resources", 0, "ComputeInstance", "size") -> "resources[0].ComputeInstance.size"."""
    parts = loc[1:] if loc and loc[0] == "body" else loc
    path = ""
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path or "(body)"


async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _schema_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"field": field_path(e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return error_response(
        422, "schema_validation_failed", "The request does not match the IR schema.", details
    )


async def _busy(_: Request, exc: PipelineBusyError) -> JSONResponse:
    return error_response(409, "generation_in_progress", str(exc))


async def _run_not_found(_: Request, exc: RunNotFoundError) -> JSONResponse:
    return error_response(404, "run_not_found", str(exc))


async def _already_approved(_: Request, exc: AlreadyApprovedError) -> JSONResponse:
    return error_response(
        409, "already_approved", str(exc), {"approval_id": exc.existing.approval_id}
    )


async def _not_approvable(_: Request, exc: RunNotApprovableError) -> JSONResponse:
    return error_response(422, exc.code, str(exc))


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    return error_response(exc.status_code, code, str(exc.detail))


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    logger.error("Unhandled error in request %s\n%s", request_id, safe_traceback(exc))
    response = error_response(
        500, "internal_error", "An unexpected error occurred. See the server log."
    )
    response.headers["X-Request-ID"] = request_id
    return response


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _schema_error)
    app.add_exception_handler(PipelineBusyError, _busy)
    app.add_exception_handler(RunNotFoundError, _run_not_found)
    app.add_exception_handler(AlreadyApprovedError, _already_approved)
    app.add_exception_handler(RunNotApprovableError, _not_approvable)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected)
