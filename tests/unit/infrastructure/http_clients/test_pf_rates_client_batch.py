"""Tests for PfRatesClient batch lookups."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from payroll.infrastructure.http.pf_rates_client import PfRatesClient


BASE_URL = "http://pf-rates.test"


def _client() -> PfRatesClient:
    """Build a test client."""
    return PfRatesClient(BASE_URL, "test-key")


@pytest.mark.asyncio
@respx.mock
async def test_exchange_rate_batch_returns_values_and_uses_one_http_call() -> None:
    """Multiple exchange-rate pairs are sent in one POST and parsed."""
    route = respx.post(f"{BASE_URL}/exchange-rates/values").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "currency_code": "USD",
                        "rate_date": "2026-04-15",
                        "value_clp": "950.50",
                    },
                    {
                        "currency_code": "EUR",
                        "rate_date": "2026-04-15",
                        "value_clp": None,
                    },
                ]
            },
        )
    )

    result = await _client().get_exchange_rate_values(
        [("USD", date(2026, 4, 15)), ("EUR", date(2026, 4, 15))]
    )

    assert result == {
        ("USD", date(2026, 4, 15)): Decimal("950.50"),
        ("EUR", date(2026, 4, 15)): None,
    }
    assert route.call_count == 1
    assert len(route.calls[0].request.content) > 0


@pytest.mark.asyncio
@respx.mock
async def test_exchange_rate_batch_skips_cached_pairs() -> None:
    """Cached pairs are not sent to the batch endpoint."""
    route = respx.post(f"{BASE_URL}/exchange-rates/values").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "currency_code": "EUR",
                        "rate_date": "2026-04-15",
                        "value_clp": "1020.00",
                    }
                ]
            },
        )
    )
    client = _client()
    client._cache.set(("exchange_rate", "USD", date(2026, 4, 15)), Decimal("950"))

    result = await client.get_exchange_rate_values(
        [("USD", date(2026, 4, 15)), ("EUR", date(2026, 4, 15))]
    )

    assert result[("USD", date(2026, 4, 15))] == Decimal("950")
    assert result[("EUR", date(2026, 4, 15))] == Decimal("1020.00")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_economic_index_batch_returns_values() -> None:
    """Multiple economic-index pairs are parsed from one POST response."""
    route = respx.post(f"{BASE_URL}/economic-indices/values").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "code": "IPC_CL",
                        "period_year": 2026,
                        "period_month": 1,
                        "index_value": "112.5",
                    }
                ]
            },
        )
    )

    client = _client()
    result = await client.get_economic_index_values([("IPC_CL", 2026, 1)])
    cached_result = await client.get_economic_index_values([("IPC_CL", 2026, 1)])

    assert result == {("IPC_CL", 2026, 1): Decimal("112.5")}
    assert cached_result == result
    assert route.call_count == 1
