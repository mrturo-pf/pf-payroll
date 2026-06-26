"""Tests for the shared pf_rates_get HTTP helper."""

import pytest
import respx
import httpx

from payroll.application.errors import PayrollDependencyError
from payroll.infrastructure.http._http_client import pf_rates_get

_URL = "http://pf-rates.test/some-endpoint"
_HEADERS = {"X-API-Key": "test-key"}


@pytest.mark.asyncio
@respx.mock
async def test_pf_rates_get_returns_json_on_200() -> None:
    """Returns parsed JSON on a 200 response."""
    respx.get(_URL).mock(return_value=httpx.Response(200, json={"value": "123"}))
    result = await pf_rates_get(_URL, {}, _HEADERS, label="test")
    assert result == {"value": "123"}


@pytest.mark.asyncio
@respx.mock
async def test_pf_rates_get_returns_none_on_404() -> None:
    """Returns None on 404."""
    respx.get(_URL).mock(return_value=httpx.Response(404))
    result = await pf_rates_get(_URL, {}, _HEADERS, label="test")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_pf_rates_get_raises_on_5xx() -> None:
    """Raises PayrollDependencyError on server errors."""
    respx.get(_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(PayrollDependencyError, match="HTTP 503"):
        await pf_rates_get(_URL, {}, _HEADERS, label="test entity")


@pytest.mark.asyncio
@respx.mock
async def test_pf_rates_get_raises_on_network_error() -> None:
    """Raises PayrollDependencyError on network failures."""
    respx.get(_URL).mock(side_effect=httpx.ConnectError("unreachable"))
    with pytest.raises(PayrollDependencyError, match="Network error"):
        await pf_rates_get(_URL, {}, _HEADERS, label="test entity")
