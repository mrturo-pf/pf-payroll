"""Healthcheck routes."""

import time

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

# Captured once, at process start (module import time), to compute uptime.
_START_TIME = time.monotonic()


class HealthRead(BaseModel):
    """Represent Health Read."""

    status: str
    service: str
    uptime_seconds: float


@router.get("/health", response_model=HealthRead)
async def health() -> HealthRead:
    """Return service health status, including process uptime."""
    return HealthRead(
        status="ok",
        service="pf-payroll",
        uptime_seconds=round(time.monotonic() - _START_TIME, 3),
    )
