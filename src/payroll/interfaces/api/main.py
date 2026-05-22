"""FastAPI application entrypoint."""

from fastapi import FastAPI

from payroll.interfaces.api.routes.health import router as health_router

app = FastAPI(title="Payroll API")
app.include_router(health_router)
