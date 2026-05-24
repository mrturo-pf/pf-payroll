"""Tests for API application startup hooks."""

import asyncio
from types import SimpleNamespace

import pytest

from payroll.interfaces.api import main


@pytest.mark.asyncio
async def test_run_startup_market_data_sync_uses_session_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test startup sync uses its own session and logs completion."""
    info_calls: list[tuple[str, dict[str, int | str]]] = []
    session = object()

    class FakeSessionManager:
        """Test double for session manager."""

        async def __aenter__(self) -> object:
            """Enter the async context manager."""
            return session

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            """Exit the async context manager."""

    class FakeUseCase:
        """Test double for startup sync use case."""

        async def execute(self) -> object:
            """Handle execute."""
            return SimpleNamespace(
                requested_exchange_rates=10,
                requested_economic_indices=2,
                upserted_exchange_rates=8,
                upserted_economic_indices=2,
            )

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(main, "SessionLocal", lambda: FakeSessionManager())
    monkeypatch.setattr(
        main, "build_startup_market_data_sync", lambda value: FakeUseCase()
    )
    monkeypatch.setattr(
        main.logger,
        "info",
        lambda event, **kwargs: info_calls.append((event, kwargs)),
    )

    await main.run_startup_market_data_sync()

    assert info_calls == [
        ("startup_market_data_sync_started", {}),
        (
            "startup_market_data_sync_completed",
            {
                "requested_exchange_rates": 10,
                "requested_economic_indices": 2,
                "upserted_exchange_rates": 8,
                "upserted_economic_indices": 2,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_run_startup_market_data_sync_skips_under_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test startup sync is skipped during pytest app startups."""
    called = False

    def fake_session_local() -> object:
        """Track unexpected session usage."""
        nonlocal called
        called = True
        raise AssertionError("session should not be used")

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "active")
    monkeypatch.setattr(main, "SessionLocal", fake_session_local)

    await main.run_startup_market_data_sync()

    assert called is False


@pytest.mark.asyncio
async def test_run_startup_market_data_sync_logs_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test startup sync logs failures instead of failing silently."""
    exception_calls: list[tuple[str, dict[str, str]]] = []

    class FakeSessionManager:
        """Test double for session manager."""

        async def __aenter__(self) -> object:
            """Enter the async context manager."""
            return object()

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            """Exit the async context manager."""

    class FakeUseCase:
        """Test double for failing startup sync use case."""

        async def execute(self) -> object:
            """Handle execute."""
            raise RuntimeError("sync exploded")

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(main, "SessionLocal", lambda: FakeSessionManager())
    monkeypatch.setattr(
        main, "build_startup_market_data_sync", lambda value: FakeUseCase()
    )
    monkeypatch.setattr(main.logger, "info", lambda event, **kwargs: None)
    monkeypatch.setattr(
        main.logger,
        "exception",
        lambda event, **kwargs: exception_calls.append((event, kwargs)),
    )

    await main.run_startup_market_data_sync()

    assert exception_calls == [
        ("startup_market_data_sync_failed", {"error": "sync exploded"})
    ]


@pytest.mark.asyncio
async def test_run_startup_market_data_sync_logs_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test startup sync logs cancellation before re-raising it."""
    info_calls: list[tuple[str, dict[str, int | str]]] = []

    class FakeSessionManager:
        """Test double for session manager."""

        async def __aenter__(self) -> object:
            """Enter the async context manager."""
            return object()

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            """Exit the async context manager."""

    class FakeUseCase:
        """Test double for cancelled startup sync use case."""

        async def execute(self) -> object:
            """Handle execute."""
            raise asyncio.CancelledError()

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(main, "SessionLocal", lambda: FakeSessionManager())
    monkeypatch.setattr(
        main, "build_startup_market_data_sync", lambda value: FakeUseCase()
    )
    monkeypatch.setattr(
        main.logger,
        "info",
        lambda event, **kwargs: info_calls.append((event, kwargs)),
    )

    with pytest.raises(asyncio.CancelledError):
        await main.run_startup_market_data_sync()

    assert info_calls == [
        ("startup_market_data_sync_started", {}),
        ("startup_market_data_sync_cancelled", {}),
    ]


@pytest.mark.asyncio
async def test_api_lifespan_keeps_completed_sync_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test lifespan completes cleanly when sync finishes early."""

    async def fake_runner() -> None:
        """Complete immediately."""

    fake_app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(main, "run_startup_market_data_sync", fake_runner)

    async with main.lifespan(fake_app):
        await fake_app.state.market_data_sync_task

    assert fake_app.state.market_data_sync_task.done() is True


@pytest.mark.asyncio
async def test_api_lifespan_cancels_running_sync_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test lifespan cancels the background sync task on shutdown."""
    cancelled = False

    async def fake_runner() -> None:
        """Wait until the task is cancelled."""
        nonlocal cancelled
        try:
            while True:
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            cancelled = True
            raise

    fake_app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setattr(main, "run_startup_market_data_sync", fake_runner)

    async with main.lifespan(fake_app):
        await asyncio.sleep(0)
        assert fake_app.state.market_data_sync_task.done() is False

    assert cancelled is True
