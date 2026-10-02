"""Tests for the shared pf_rates_get HTTP helper."""

import asyncio

import pytest
import respx
import httpx

from payroll.application.errors import PayrollDependencyError
from payroll.infrastructure.http._http_client import pf_rates_get

_URL = "http://pf-rates.test/some-endpoint"
_HEADERS = {"X-API-Key": "test-key"}


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real retry backoff delay so retry tests run instantly."""

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


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
    """Raises PayrollDependencyError after exhausting all retry attempts."""
    route = respx.get(_URL).mock(side_effect=httpx.ConnectError("unreachable"))
    with pytest.raises(PayrollDependencyError, match="Network error.*after 3 attempts"):
        await pf_rates_get(_URL, {}, _HEADERS, label="test entity")
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_pf_rates_get_retries_and_succeeds_on_second_attempt() -> None:
    """A single transient network error is retried, not surfaced as a failure."""
    route = respx.get(_URL).mock(
        side_effect=[
            httpx.ConnectError("timeout"),
            httpx.Response(200, json={"value": "456"}),
        ]
    )
    result = await pf_rates_get(_URL, {}, _HEADERS, label="test entity")
    assert result == {"value": "456"}
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_pf_rates_get_retries_and_succeeds_on_third_attempt() -> None:
    """Two consecutive transient network errors are still retried successfully."""
    route = respx.get(_URL).mock(
        side_effect=[
            httpx.ConnectError("timeout"),
            httpx.ReadTimeout("timeout"),
            httpx.Response(200, json={"value": "789"}),
        ]
    )
    result = await pf_rates_get(_URL, {}, _HEADERS, label="test entity")
    assert result == {"value": "789"}
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_pf_rates_get_never_retries_404() -> None:
    """A 404 is a final answer -- no retry, single call."""
    route = respx.get(_URL).mock(return_value=httpx.Response(404))
    result = await pf_rates_get(_URL, {}, _HEADERS, label="test")
    assert result is None
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_pf_rates_get_never_retries_http_error_status() -> None:
    """A real HTTP error status is final -- no retry, single call."""
    route = respx.get(_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(PayrollDependencyError, match="HTTP 503"):
        await pf_rates_get(_URL, {}, _HEADERS, label="test entity")
    assert route.call_count == 1
