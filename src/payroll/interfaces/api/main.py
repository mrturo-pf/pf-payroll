"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from payroll.application.errors import PayrollError
from payroll.interfaces.api.routes.health import router as health_router
from payroll.interfaces.api.routes.payroll import router as payroll_router
from payroll.interfaces.api.routes.reference_data import router as reference_data_router
from payroll.interfaces.api.security import verify_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run application lifespan hooks."""
    yield


app = FastAPI(title="Payroll API", lifespan=lifespan)
app.include_router(health_router)
app.include_router(payroll_router, dependencies=[Depends(verify_api_key)])
app.include_router(reference_data_router, dependencies=[Depends(verify_api_key)])


@app.exception_handler(PayrollError)
async def payroll_error_handler(_request: Request, exc: PayrollError) -> JSONResponse:
    """Convert PayrollError subclasses to structured JSON error responses."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc)},
    )
