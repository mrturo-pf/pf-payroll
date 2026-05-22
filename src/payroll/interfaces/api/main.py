"""FastAPI application entrypoint."""

from fastapi import FastAPI

from payroll.interfaces.api.routes.health import router as health_router
from payroll.interfaces.api.routes.market_data import router as market_data_router
from payroll.interfaces.api.routes.payroll import router as payroll_router
from payroll.interfaces.api.routes.reference_data import router as reference_data_router

app = FastAPI(title="Payroll API")
app.include_router(health_router)
app.include_router(market_data_router)
app.include_router(payroll_router)
app.include_router(reference_data_router)
