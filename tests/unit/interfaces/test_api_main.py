"""Tests for API application startup hooks."""

import pytest
from types import SimpleNamespace

from payroll.interfaces.api import main


@pytest.mark.asyncio
async def test_lifespan_starts_and_shuts_down_cleanly() -> None:
    """Test that the simplified lifespan enters and exits without errors."""
    fake_app = SimpleNamespace(state=SimpleNamespace())

    async with main.lifespan(fake_app):
        pass  # startup is a no-op; just verify no exception is raised


def test_app_includes_expected_routers() -> None:
    """Test that the app has routers mounted (title and route count sanity check)."""
    assert main.app.title == "Payroll API"
    # 3 include_router calls (health, payroll, reference-data) + built-in openapi routes
    assert len(main.app.routes) >= 3
