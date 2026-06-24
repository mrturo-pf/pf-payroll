"""Tests for payroll market-data background task helpers."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from payroll.application.dto import (
    MarketDataSyncRequestDTO,
    SyncRecentMarketDataResultDTO,
)
from payroll.interfaces.api import background_tasks


class _FakeSessionManager:
    """Test double for session manager."""

    async def __aenter__(self) -> object:
        """Enter the async context manager."""
        return object()

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Exit the async context manager."""


def _patch_sync_task(
    monkeypatch: pytest.MonkeyPatch,
    use_case: object,
    info_logger: object,
) -> None:
    """Patch the background task infrastructure for sync tests."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(background_tasks, "SessionLocal", lambda: _FakeSessionManager())
    monkeypatch.setattr(
        background_tasks,
        "build_market_data_sync_use_case",
        lambda session: use_case,
    )
    monkeypatch.setattr(background_tasks.logger, "info", info_logger)


def test_market_data_sync_request_helpers_detect_work() -> None:
    """Test helper functions count pending sync work."""
    empty_request = MarketDataSyncRequestDTO()
    populated_request = MarketDataSyncRequestDTO(
        exchange_rate_dates={"UF": [date(2026, 1, 31)]},
        economic_index_periods={"IPC_CL": [(2026, 1), (2026, 2)]},
    )

    assert background_tasks.has_market_data_sync_work(None) is False
    assert background_tasks.has_market_data_sync_work(empty_request) is False
    assert background_tasks.count_requested_exchange_rates(populated_request) == 1
    assert background_tasks.count_requested_economic_indices(populated_request) == 2
    assert background_tasks.has_market_data_sync_work(populated_request) is True


@pytest.mark.asyncio
async def test_run_payroll_market_data_sync_logs_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test payroll-triggered sync uses its own session and logs completion."""
    info_calls: list[tuple[str, dict[str, int | str]]] = []
    sync_request = MarketDataSyncRequestDTO(
        exchange_rate_dates={"UF": [date(2026, 1, 31)]},
        economic_index_periods={"IPC_CL": [(2026, 1)]},
    )

    class FakeUseCase:
        """Test double for background sync use case."""

        async def execute_request(self, request: MarketDataSyncRequestDTO) -> object:
            """Handle execute request."""
            assert request is sync_request
            return SimpleNamespace(
                requested_exchange_rates=1,
                requested_economic_indices=1,
                upserted_exchange_rates=1,
                upserted_economic_indices=1,
            )

    _patch_sync_task(
        monkeypatch,
        FakeUseCase(),
        lambda event, **kwargs: info_calls.append((event, kwargs)),
    )

    await background_tasks.run_payroll_market_data_sync(sync_request)

    assert info_calls == [
        (
            "payroll_market_data_sync_started",
            {"requested_exchange_rates": 1, "requested_economic_indices": 1},
        ),
        (
            "payroll_market_data_sync_completed",
            {
                "requested_exchange_rates": 1,
                "requested_economic_indices": 1,
                "upserted_exchange_rates": 1,
                "upserted_economic_indices": 1,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_run_payroll_market_data_sync_logs_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test payroll-triggered sync logs failures instead of raising them."""
    exception_calls: list[tuple[str, dict[str, str]]] = []
    sync_request = MarketDataSyncRequestDTO(
        exchange_rate_dates={"UF": [date(2026, 1, 31)]}
    )

    class FakeUseCase:
        """Test double for failing background sync use case."""

        async def execute_request(self, request: MarketDataSyncRequestDTO) -> object:
            """Handle execute request."""
            raise RuntimeError("sync exploded")

    _patch_sync_task(monkeypatch, FakeUseCase(), lambda event, **kwargs: None)
    monkeypatch.setattr(
        background_tasks.logger,
        "exception",
        lambda event, **kwargs: exception_calls.append((event, kwargs)),
    )

    await background_tasks.run_payroll_market_data_sync(sync_request)

    assert exception_calls == [
        ("payroll_market_data_sync_failed", {"error": "sync exploded"})
    ]


@pytest.mark.asyncio
async def test_run_payroll_market_data_sync_logs_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test payroll-triggered sync logs cancellation before re-raising it."""
    info_calls: list[tuple[str, dict[str, int | str]]] = []
    sync_request = MarketDataSyncRequestDTO(
        exchange_rate_dates={"UF": [date(2026, 1, 31)]}
    )

    class FakeUseCase:
        """Test double for cancelled background sync use case."""

        async def execute_request(self, request: MarketDataSyncRequestDTO) -> object:
            """Handle execute request."""
            raise asyncio.CancelledError()

    _patch_sync_task(
        monkeypatch,
        FakeUseCase(),
        lambda event, **kwargs: info_calls.append((event, kwargs)),
    )

    with pytest.raises(asyncio.CancelledError):
        await background_tasks.run_payroll_market_data_sync(sync_request)

    assert info_calls == [
        (
            "payroll_market_data_sync_started",
            {"requested_exchange_rates": 1, "requested_economic_indices": 0},
        ),
        ("payroll_market_data_sync_cancelled", {}),
    ]


@pytest.mark.asyncio
async def test_run_payroll_market_data_sync_skips_when_disabled_or_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test payroll-triggered sync skips under pytest or without work."""
    called = False

    def fake_session_local() -> object:
        """Track unexpected session usage."""
        nonlocal called
        called = True
        raise AssertionError("session should not be used")

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "active")
    monkeypatch.setattr(background_tasks, "SessionLocal", fake_session_local)

    await background_tasks.run_payroll_market_data_sync(MarketDataSyncRequestDTO())

    assert called is False


@pytest.mark.asyncio
async def test_sync_payroll_market_data_now_returns_remaining_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test immediate payroll sync returns the unresolved remainder."""
    sync_request = MarketDataSyncRequestDTO(
        exchange_rate_dates={"UF": [date(2026, 1, 31)]}
    )

    class FakeUseCase:
        """Test double for immediate sync use case."""

        async def execute_request_and_collect_remaining(
            self, request: MarketDataSyncRequestDTO
        ) -> tuple[SyncRecentMarketDataResultDTO, MarketDataSyncRequestDTO | None]:
            """Handle execute request and collect remaining."""
            assert request is sync_request
            return (
                SyncRecentMarketDataResultDTO(
                    requested_exchange_rates=1,
                    requested_economic_indices=0,
                    upserted_exchange_rates=1,
                    upserted_economic_indices=0,
                ),
                None,
            )

    monkeypatch.setattr(background_tasks, "SessionLocal", lambda: _FakeSessionManager())
    monkeypatch.setattr(
        background_tasks,
        "build_market_data_sync_use_case",
        lambda session: FakeUseCase(),
    )

    result = await background_tasks.sync_payroll_market_data_now(sync_request)

    assert result == (
        SyncRecentMarketDataResultDTO(
            requested_exchange_rates=1,
            requested_economic_indices=0,
            upserted_exchange_rates=1,
            upserted_economic_indices=0,
        ),
        None,
    )


@pytest.mark.asyncio
async def test_schedule_and_cancel_payroll_market_data_sync_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test scheduling registers tasks and shutdown cancellation stops them."""
    queued_calls: list[tuple[str, dict[str, int]]] = []
    started = asyncio.Event()
    cancelled = asyncio.Event()
    app = SimpleNamespace(state=SimpleNamespace())
    sync_request = MarketDataSyncRequestDTO(
        exchange_rate_dates={"UF": [date(2026, 1, 31)]}
    )

    async def fake_runner(request: MarketDataSyncRequestDTO) -> None:
        """Wait until cancelled."""
        assert request is sync_request
        started.set()
        try:
            while True:
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(background_tasks, "run_payroll_market_data_sync", fake_runner)
    monkeypatch.setattr(
        background_tasks.logger,
        "info",
        lambda event, **kwargs: queued_calls.append((event, kwargs)),
    )

    assert background_tasks.ensure_payroll_market_data_sync_tasks(app) == set()
    background_tasks.schedule_payroll_market_data_sync(app, sync_request)
    await started.wait()

    assert len(background_tasks.ensure_payroll_market_data_sync_tasks(app)) == 1
    assert queued_calls == [
        (
            "payroll_market_data_sync_queued",
            {"requested_exchange_rates": 1, "requested_economic_indices": 0},
        )
    ]

    await background_tasks.cancel_payroll_market_data_sync_tasks(app)

    assert cancelled.is_set() is True
    assert background_tasks.ensure_payroll_market_data_sync_tasks(app) == set()


@pytest.mark.asyncio
async def test_cancel_payroll_market_data_sync_tasks_ignores_completed_tasks() -> None:
    """Test shutdown cancellation ignores tasks that already finished."""
    app = SimpleNamespace(state=SimpleNamespace())

    async def fake_runner() -> None:
        """Complete immediately."""

    task = asyncio.create_task(fake_runner())
    await task
    app.state.payroll_market_data_sync_tasks = {task}

    await background_tasks.cancel_payroll_market_data_sync_tasks(app)

    assert task.done() is True
