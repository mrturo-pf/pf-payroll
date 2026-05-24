"""FastAPI application entrypoint."""

import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from payroll.infrastructure.db.session import SessionLocal
from payroll.infrastructure.logging.logger import logger
from payroll.interfaces.api.dependencies import build_startup_market_data_sync
from payroll.interfaces.api.background_tasks import (
    cancel_payroll_market_data_sync_tasks,
    ensure_payroll_market_data_sync_tasks,
)
from payroll.interfaces.api.routes.health import router as health_router
from payroll.interfaces.api.routes.market_data import router as market_data_router
from payroll.interfaces.api.routes.payroll import router as payroll_router
from payroll.interfaces.api.routes.reference_data import router as reference_data_router


async def run_startup_market_data_sync() -> None:
    """Run startup market-data sync in the background."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return

    logger.info("startup_market_data_sync_started")
    try:
        async with SessionLocal() as session:
            result = await build_startup_market_data_sync(session).execute()
    except asyncio.CancelledError:
        logger.info("startup_market_data_sync_cancelled")
        raise
    except Exception as exc:
        logger.exception(
            "startup_market_data_sync_failed",
            error=str(exc),
        )
        return

    logger.info(
        "startup_market_data_sync_completed",
        requested_exchange_rates=result.requested_exchange_rates,
        requested_economic_indices=result.requested_economic_indices,
        upserted_exchange_rates=result.upserted_exchange_rates,
        upserted_economic_indices=result.upserted_economic_indices,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run application lifespan hooks."""
    ensure_payroll_market_data_sync_tasks(app)
    sync_task = asyncio.create_task(run_startup_market_data_sync())
    app.state.market_data_sync_task = sync_task
    try:
        yield
    finally:
        await cancel_payroll_market_data_sync_tasks(app)
        if not sync_task.done():
            sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await sync_task


app = FastAPI(title="Payroll API", lifespan=lifespan)
app.include_router(health_router)
app.include_router(market_data_router)
app.include_router(payroll_router)
app.include_router(reference_data_router)
