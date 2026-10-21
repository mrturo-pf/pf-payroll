"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from payroll.application.errors import PayrollError
from payroll.infrastructure.logging.logger import logger
from payroll.interfaces.api.routes.health import router as health_router
from payroll.interfaces.api.routes.employers_contracts import (
    router as employers_contracts_router,
)
from payroll.interfaces.api.routes.payroll import router as payroll_router
from payroll.interfaces.api.routes.payroll_export import (
    router as payroll_export_router,
)
from payroll.interfaces.api.routes.pdf_templates import router as pdf_templates_router
from payroll.interfaces.api.routes.reference_data import router as reference_data_router
from payroll.interfaces.api.security import verify_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run application lifespan hooks."""
    yield


app = FastAPI(title="Payroll API", lifespan=lifespan)
app.include_router(health_router)
# payroll_export_router's and pdf_templates_router's static "/payroll/spreadsheet",
# "/payroll/spreadsheet/template", and "/payroll/templates" paths must be registered
# *before* payroll_router's dynamic "/payroll/{period_id}" -- Starlette matches routes
# in registration order, and a single-segment path param with no type converter
# matches any string, "spreadsheet"/"templates" included. Swapping this order would
# make GET /payroll/templates 422 on int("templates") instead of ever reaching the
# template-management routes.
app.include_router(payroll_export_router, dependencies=[Depends(verify_api_key)])
app.include_router(pdf_templates_router, dependencies=[Depends(verify_api_key)])
app.include_router(employers_contracts_router, dependencies=[Depends(verify_api_key)])
app.include_router(payroll_router, dependencies=[Depends(verify_api_key)])
app.include_router(reference_data_router, dependencies=[Depends(verify_api_key)])


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return actionable details when a request cannot be parsed or validated."""
    logger.warning(
        "request_validation_failed",
        method=request.method,
        path=request.url.path,
        errors=exc.errors(),
    )
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Request validation failed.",
            "errors": jsonable_encoder(exc.errors()),
        },
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected failures and expose their actionable error detail.

    The exception type and message are returned for diagnosis while the full
    traceback stays in structured application logs.
    """
    logger.exception(
        "unexpected_request_error",
        method=request.method,
        path=request.url.path,
        error_type=type(exc).__name__,
        error=str(exc),
    )
    message = str(exc).strip() or "No additional error detail was provided."
    return JSONResponse(
        status_code=500,
        content={
            "detail": (f"Unexpected server error. {type(exc).__name__}: {message}"),
            "path": request.url.path,
        },
    )


@app.exception_handler(PayrollError)
async def payroll_error_handler(_request: Request, exc: PayrollError) -> JSONResponse:
    """Convert PayrollError subclasses to structured JSON error responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc)},
    )
