"""Shared test doubles for SQLAlchemy async session and API DI wiring."""

from collections.abc import AsyncIterator
from types import ModuleType

import pytest


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
