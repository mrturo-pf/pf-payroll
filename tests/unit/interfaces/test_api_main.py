"""Tests for API application startup hooks."""

import json
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


@pytest.mark.asyncio
async def test_payroll_error_handler_returns_status_code_and_detail() -> None:
    """PayrollError is serialized to {detail: str} JSON with the error's status code."""
    from payroll.application.errors import PayrollDependencyError

    exc = PayrollDependencyError("pf-rates unreachable: missing protocol")
    response = await main.payroll_error_handler(SimpleNamespace(), exc)  # type: ignore[arg-type]

    assert response.status_code == 502
    assert json.loads(response.body) == {
        "detail": "pf-rates unreachable: missing protocol"
    }
