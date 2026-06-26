"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from payroll.interfaces.api.routes.health import router as health_router
from payroll.interfaces.api.routes.payroll import router as payroll_router
from payroll.interfaces.api.routes.reference_data import router as reference_data_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run application lifespan hooks."""
    yield


app = FastAPI(title="Payroll API", lifespan=lifespan)
app.include_router(health_router)
app.include_router(payroll_router)
app.include_router(reference_data_router)
