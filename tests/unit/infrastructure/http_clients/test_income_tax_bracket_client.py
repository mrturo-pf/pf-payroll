"""Tests for IncomeTaxBracketClient."""

from datetime import date
from decimal import Decimal

import pytest
import respx
import httpx

from payroll.application.errors import PayrollDependencyError
from payroll.domain.taxes import IncomeTaxBracket
from payroll.infrastructure.http.income_tax_bracket_client import IncomeTaxBracketClient


BASE_URL = "http://pf-rates.test"
_BRACKET_JSON = {
    "valid_from": "2026-01-01",
    "valid_to": None,
    "lower_bound_utm": "13.5",
    "upper_bound_utm": "30.0",
    "marginal_rate": "0.040000",
    "rebate_utm": "0.5400",
}


def _client(
    ttl: int = 300, clock_values: list[float] | None = None
) -> IncomeTaxBracketClient:
    """Build a client with an optional deterministic clock."""
    if clock_values is not None:
        values = iter(clock_values)
        return IncomeTaxBracketClient(
            BASE_URL, "test-key", ttl, clock=lambda: next(values)
        )
    return IncomeTaxBracketClient(BASE_URL, "test-key", ttl)


@pytest.mark.asyncio
@respx.mock
async def test_get_income_tax_bracket_maps_response_to_domain_object() -> None:
    """200 response is correctly mapped to an IncomeTaxBracket domain object."""
    respx.get(f"{BASE_URL}/income-tax-brackets").mock(
        return_value=httpx.Response(200, json=_BRACKET_JSON)
    )
    bracket = await _client().get_income_tax_bracket(date(2026, 4, 30), Decimal("20.5"))
    assert isinstance(bracket, IncomeTaxBracket)
    assert bracket.marginal_rate == Decimal("0.040000")
    assert bracket.valid_to is None
    assert bracket.upper_bound_utm == Decimal("30.0")


@pytest.mark.asyncio
@respx.mock
async def test_get_income_tax_bracket_returns_none_on_404() -> None:
    """Returns None when pf-rates responds with 404."""
    respx.get(f"{BASE_URL}/income-tax-brackets").mock(return_value=httpx.Response(404))
    bracket = await _client().get_income_tax_bracket(date(2026, 4, 30), Decimal("20.5"))
    assert bracket is None


@pytest.mark.asyncio
@respx.mock
async def test_get_income_tax_bracket_raises_on_5xx() -> None:
    """Raises PayrollDependencyError on server errors."""
    respx.get(f"{BASE_URL}/income-tax-brackets").mock(return_value=httpx.Response(503))
    with pytest.raises(PayrollDependencyError, match="pf-rates returned HTTP 503"):
        await _client().get_income_tax_bracket(date(2026, 4, 30), Decimal("20.5"))


@pytest.mark.asyncio
@respx.mock
async def test_get_income_tax_bracket_caches_within_ttl() -> None:
    """Multiple identical calls within TTL produce exactly one HTTP request."""
    clock = [0.0, 1.0, 2.0]
    route = respx.get(f"{BASE_URL}/income-tax-brackets").mock(
        return_value=httpx.Response(200, json=_BRACKET_JSON)
    )
    client = _client(ttl=300, clock_values=clock)
    for _ in range(3):
        await client.get_income_tax_bracket(date(2026, 4, 30), Decimal("20.5"))
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_income_tax_bracket_refetches_after_ttl_expiry() -> None:
    """A new HTTP call is made after the cache entry expires."""
    # set at t=0 (expiry=2); get at t=5 (5>=2 → expired); set at t=5
    clock = [0.0, 5.0, 5.0]
    route = respx.get(f"{BASE_URL}/income-tax-brackets").mock(
        return_value=httpx.Response(200, json=_BRACKET_JSON)
    )
    client = _client(ttl=2, clock_values=clock)
    await client.get_income_tax_bracket(date(2026, 4, 30), Decimal("20.5"))
    await client.get_income_tax_bracket(date(2026, 4, 30), Decimal("20.5"))
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_get_income_tax_bracket_raises_on_network_error() -> None:
    """Raises PayrollDependencyError on network failures."""
    respx.get(f"{BASE_URL}/income-tax-brackets").mock(
        side_effect=httpx.ConnectError("timeout")
    )
    with pytest.raises(PayrollDependencyError, match="Network error"):
        await _client().get_income_tax_bracket(date(2026, 4, 30), Decimal("20.5"))


@pytest.mark.asyncio
@respx.mock
async def test_get_income_tax_bracket_caches_none_on_404() -> None:
    """A 404 None result is cached to avoid repeated requests."""
    route = respx.get(f"{BASE_URL}/income-tax-brackets").mock(
        return_value=httpx.Response(404)
    )
    client = _client()
    r1 = await client.get_income_tax_bracket(date(2026, 4, 30), Decimal("20.5"))
    r2 = await client.get_income_tax_bracket(date(2026, 4, 30), Decimal("20.5"))
    assert r1 is None
    assert r2 is None
    assert route.call_count == 1
