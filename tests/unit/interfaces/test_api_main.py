"""Tests for API application startup hooks."""

import json
import pytest
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
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


def _request(path: str = "/payroll") -> Request:
    """Build a minimal request for exception-handler unit tests."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1234),
            "root_path": "",
        }
    )


@pytest.mark.asyncio
async def test_unexpected_error_handler_returns_diagnostic_detail() -> None:
    """Unexpected failures include type, message, and request path."""
    response = await main.unexpected_error_handler(
        _request(), RuntimeError("database connection failed")
    )

    assert response.status_code == 500
    assert response.body is not None
    assert b"RuntimeError: database connection failed" in response.body
    assert b'"path":"/payroll"' in response.body


@pytest.mark.asyncio
async def test_validation_error_handler_returns_structured_errors() -> None:
    """Malformed or invalid requests return a useful validation payload."""
    error = RequestValidationError(
        [{"type": "json_invalid", "loc": ("body",), "msg": "bad JSON", "input": "x"}]
    )

    response = await main.request_validation_error_handler(_request(), error)

    assert response.status_code == 422
    assert response.body is not None
    assert b'"detail":"Request validation failed."' in response.body
    assert b'"type":"json_invalid"' in response.body


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
