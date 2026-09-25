"""Shared test doubles for SQLAlchemy async session and API DI wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import ModuleType

import pytest


class FakeAllMixin:
    """Mixin that provides all() for joined-row results.

    Subclasses must set self._joined_rows in their __init__.
    """

    _joined_rows: list[tuple[object, object]]

    def all(self) -> list[tuple[object, object]]:
        """Handle all."""
        return self._joined_rows


class FakeResultsQueueBase:
    """Base that holds a queue of FakeResult objects for FakeSession subclasses."""

    def __init__(self, results: list[object]) -> None:
        """Initialize the instance."""
        self._results = results


class FakeScalarResult:
    """Test double for Scalar Result."""

    def __init__(self, rows: list[object]) -> None:
        """Initialize the instance."""
        self._rows = rows

    def all(self) -> list[object]:
        """Handle all."""
        return self._rows


async def assert_get_session_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    dependencies_mod: ModuleType,
) -> object:
    """Verify get_session() manages the async context and return the fake session.

    Patches SessionLocal on *dependencies_mod*, drives the async-generator
    lifecycle (yield → StopAsyncIteration → __aexit__ called), asserts all three
    invariants, and returns the fake session token for subsequent assertions.
    """
    fake_session = object()
    exited: list[bool] = [False]

    class _FakeSessionManager:
        async def __aenter__(self) -> object:
            """Enter the async context manager."""
            return fake_session

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            """Exit the async context manager."""
            exited[0] = True

    monkeypatch.setattr(dependencies_mod, "SessionLocal", lambda: _FakeSessionManager())

    iterator: AsyncIterator[object] = dependencies_mod.get_session()
    assert await anext(iterator) is fake_session
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    assert exited[0] is True

    return fake_session


async def assert_get_transactional_session_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    dependencies_mod: ModuleType,
) -> object:
    """Verify get_transactional_session() manages the async context.

    Mirrors assert_get_session_lifecycle() above, but patches
    open_transactional_session (an @asynccontextmanager, not a session
    factory) since that is what get_transactional_session() delegates to.
    """
    fake_scope = object()
    exited: list[bool] = [False]

    @asynccontextmanager
    async def _fake_open_transactional_session() -> AsyncIterator[object]:
        try:
            yield fake_scope
        finally:
            exited[0] = True

    monkeypatch.setattr(
        dependencies_mod,
        "open_transactional_session",
        _fake_open_transactional_session,
    )

    iterator = dependencies_mod.get_transactional_session()
    assert await anext(iterator) is fake_scope
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    assert exited[0] is True

    return fake_scope
