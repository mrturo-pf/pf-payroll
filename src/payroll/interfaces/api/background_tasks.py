"""Background market-data synchronization helpers for the API layer."""

import asyncio
import os
from collections.abc import MutableSet
from contextlib import suppress

from fastapi import FastAPI

from payroll.application.dto import MarketDataSyncRequestDTO
from payroll.infrastructure.db.session import SessionLocal
from payroll.infrastructure.logging.logger import logger
from payroll.interfaces.api.dependencies import build_startup_market_data_sync


def count_requested_exchange_rates(sync_request: MarketDataSyncRequestDTO) -> int:
    """Count requested exchange-rate dates in a sync request."""
    return sum(
        len(rate_dates) for rate_dates in sync_request.exchange_rate_dates.values()
    )


def count_requested_economic_indices(sync_request: MarketDataSyncRequestDTO) -> int:
    """Count requested economic-index periods in a sync request."""
    return sum(len(periods) for periods in sync_request.economic_index_periods.values())


def has_market_data_sync_work(sync_request: MarketDataSyncRequestDTO | None) -> bool:
    """Return whether the sync request contains pending work."""
    if sync_request is None:
        return False
    return bool(
        count_requested_exchange_rates(sync_request)
        or count_requested_economic_indices(sync_request)
    )


async def run_payroll_market_data_sync(sync_request: MarketDataSyncRequestDTO) -> None:
    """Run a payroll-triggered market-data gap sync in the background."""
    if "PYTEST_CURRENT_TEST" in os.environ or not has_market_data_sync_work(
        sync_request
    ):
        return

    logger.info(
        "payroll_market_data_sync_started",
        requested_exchange_rates=count_requested_exchange_rates(sync_request),
        requested_economic_indices=count_requested_economic_indices(sync_request),
    )
    try:
        async with SessionLocal() as session:
            result = await build_startup_market_data_sync(session).execute_request(
                sync_request
            )
    except asyncio.CancelledError:
        logger.info("payroll_market_data_sync_cancelled")
        raise
    except Exception as exc:
        logger.exception(
            "payroll_market_data_sync_failed",
            error=str(exc),
        )
        return

    logger.info(
        "payroll_market_data_sync_completed",
        requested_exchange_rates=result.requested_exchange_rates,
        requested_economic_indices=result.requested_economic_indices,
        upserted_exchange_rates=result.upserted_exchange_rates,
        upserted_economic_indices=result.upserted_economic_indices,
    )


def schedule_payroll_market_data_sync(
    app: FastAPI, sync_request: MarketDataSyncRequestDTO | None
) -> None:
    """Schedule a payroll-triggered market-data gap sync for the current app."""
    if "PYTEST_CURRENT_TEST" in os.environ or not has_market_data_sync_work(
        sync_request
    ):
        return
    request = sync_request
    assert request is not None

    logger.info(
        "payroll_market_data_sync_queued",
        requested_exchange_rates=count_requested_exchange_rates(request),
        requested_economic_indices=count_requested_economic_indices(request),
    )
    task = asyncio.create_task(run_payroll_market_data_sync(request))
    tracked_tasks = ensure_payroll_market_data_sync_tasks(app)
    tracked_tasks.add(task)
    task.add_done_callback(tracked_tasks.discard)


def ensure_payroll_market_data_sync_tasks(
    app: FastAPI,
) -> MutableSet[asyncio.Task[None]]:
    """Return the task registry used to track payroll-triggered syncs."""
    tracked_tasks = getattr(app.state, "payroll_market_data_sync_tasks", None)
    if tracked_tasks is None:
        tracked_tasks = set()
        app.state.payroll_market_data_sync_tasks = tracked_tasks
    return tracked_tasks


async def cancel_payroll_market_data_sync_tasks(app: FastAPI) -> None:
    """Cancel any still-running payroll-triggered sync tasks."""
    tracked_tasks = list(ensure_payroll_market_data_sync_tasks(app))
    for task in tracked_tasks:
        if task.done():
            continue
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
