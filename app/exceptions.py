import logging
import traceback
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unexpected exceptions.
    Logs the full traceback internally but returns a safe generic message to clients.
    """
    logger.error(
        f"Unhandled exception at {request.url}: {exc}\n{traceback.format_exc()}"
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"status": "error", "error_message": "Internal Server Error"},
    )

async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handler for FastAPI/Starlette HTTP exceptions (like 404, 401, 403, etc)."""
    error_message = exc.detail
    if isinstance(error_message, list) or isinstance(error_message, dict):
        # Fallback if detail isn't a simple string
        error_message = str(error_message)

    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "error_message": error_message},
    )

async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handler for Pydantic request validation errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "status": "error",
            "error_message": "Validation Error",
            "details": exc.errors(),
        },
    )

def add_exception_handlers(app: FastAPI) -> None:
    """Registers all custom exception handlers to the FastAPI app."""
    app.add_exception_handler(Exception, generic_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
