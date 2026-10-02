"""Tests for PfRatesClient."""

import asyncio
from datetime import date
from decimal import Decimal

import pytest
import respx
import httpx

from payroll.application.errors import PayrollDependencyError
from payroll.infrastructure.http.pf_rates_client import (
    PfRatesClient,
    _normalize_exchange_rate_date,
)


BASE_URL = "http://pf-rates.test"


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real retry backoff delay so retry tests run instantly."""

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)


def _client(ttl: int = 300, clock_values: list[float] | None = None) -> PfRatesClient:
    """Build a PfRatesClient with an optional deterministic clock."""
    if clock_values is not None:
        values = iter(clock_values)
        return PfRatesClient(BASE_URL, "test-key", ttl, clock=lambda: next(values))
    return PfRatesClient(BASE_URL, "test-key", ttl)


# ---------------------------------------------------------------------------
# _normalize_exchange_rate_date
# ---------------------------------------------------------------------------


def test_normalize_utm_to_first_of_month() -> None:
    """UTM dates are normalized to the first of the month."""
    result = _normalize_exchange_rate_date("UTM", date(2026, 4, 15))
    assert result == date(2026, 4, 1)


def test_normalize_daily_code_unchanged() -> None:
    """Daily codes (USD, EUR, UF) are not normalized."""
    for code in ("USD", "EUR", "UF"):
        assert _normalize_exchange_rate_date(code, date(2026, 4, 15)) == date(
            2026, 4, 15
        )


# ---------------------------------------------------------------------------
# get_exchange_rate_value — success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_value_returns_decimal_on_200() -> None:
    """Returns a Decimal parsed from the value_clp string."""
    respx.get(f"{BASE_URL}/exchange-rates/value").mock(
        return_value=httpx.Response(200, json={"value_clp": "923.456789"})
    )
    value = await _client().get_exchange_rate_value("USD", date(2026, 4, 15))
    assert value == Decimal("923.456789")


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_value_returns_none_on_404() -> None:
    """Returns None when pf-rates responds with 404."""
    respx.get(f"{BASE_URL}/exchange-rates/value").mock(return_value=httpx.Response(404))
    value = await _client().get_exchange_rate_value("USD", date(2026, 4, 15))
    assert value is None


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_value_raises_on_5xx() -> None:
    """Raises PayrollDependencyError on server errors."""
    respx.get(f"{BASE_URL}/exchange-rates/value").mock(return_value=httpx.Response(502))
    with pytest.raises(PayrollDependencyError, match="pf-rates returned HTTP 502"):
        await _client().get_exchange_rate_value("USD", date(2026, 4, 15))


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_value_raises_on_network_error() -> None:
    """Raises PayrollDependencyError after exhausting all retry attempts."""
    route = respx.get(f"{BASE_URL}/exchange-rates/value").mock(
        side_effect=httpx.ConnectError("timeout")
    )
    with pytest.raises(PayrollDependencyError, match="Network error.*after 3 attempts"):
        await _client().get_exchange_rate_value("USD", date(2026, 4, 15))
    assert route.call_count == 3


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_value_retries_transient_network_error() -> None:
    """A transient network error is retried and does not surface to the caller.

    This is the exact scenario the 2026-10-02 investigation found in
    practice: pf-rates' own provider cascade (BCCH -> SII -> Mindicador)
    occasionally takes long enough that this client's HTTP call to
    pf-rates times out even though pf-rates itself would have eventually
    answered -- retrying once is enough to paper over that instead of
    degrading to the same `null` a genuine missing rate would produce.
    """
    route = respx.get(f"{BASE_URL}/exchange-rates/value").mock(
        side_effect=[
            httpx.ReadTimeout("timeout"),
            httpx.Response(200, json={"value_clp": "950.50"}),
        ]
    )
    result = await _client().get_exchange_rate_value("USD", date(2026, 4, 15))
    assert result == Decimal("950.50")
    assert route.call_count == 2


# ---------------------------------------------------------------------------
# get_exchange_rate_value — UTM normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_value_normalizes_utm_to_day_1() -> None:
    """UTM lookups always send rate_date as the first of the month."""
    route = respx.get(f"{BASE_URL}/exchange-rates/value").mock(
        return_value=httpx.Response(200, json={"value_clp": "65000.00"})
    )
    await _client().get_exchange_rate_value("UTM", date(2026, 4, 20))
    assert route.calls[0].request.url.params["rate_date"] == "2026-04-01"


# ---------------------------------------------------------------------------
# get_exchange_rate_value — TTL cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_value_caches_within_ttl() -> None:
    """Multiple identical lookups within TTL result in exactly one HTTP call."""
    clock = [0.0, 0.5, 1.0]  # all within TTL of 300s
    route = respx.get(f"{BASE_URL}/exchange-rates/value").mock(
        return_value=httpx.Response(200, json={"value_clp": "900.00"})
    )
    client = _client(ttl=300, clock_values=clock)
    for _ in range(3):
        await client.get_exchange_rate_value("USD", date(2026, 4, 1))
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_value_refetches_after_ttl_expiry() -> None:
    """Cache miss after TTL triggers a new HTTP call."""
    # set at t=0 (expiry=2); get at t=5 (5>=2 → expired); set at t=5
    clock = [0.0, 5.0, 5.0]
    route = respx.get(f"{BASE_URL}/exchange-rates/value").mock(
        return_value=httpx.Response(200, json={"value_clp": "900.00"})
    )
    client = _client(ttl=2, clock_values=clock)
    await client.get_exchange_rate_value("USD", date(2026, 4, 1))
    await client.get_exchange_rate_value("USD", date(2026, 4, 1))
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_value_caches_none_on_404() -> None:
    """A 404 response is cached so subsequent calls avoid extra requests."""
    route = respx.get(f"{BASE_URL}/exchange-rates/value").mock(
        return_value=httpx.Response(404)
    )
    client = _client()
    v1 = await client.get_exchange_rate_value("USD", date(2026, 4, 1))
    v2 = await client.get_exchange_rate_value("USD", date(2026, 4, 1))
    assert v1 is None
    assert v2 is None
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# get_economic_index_value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_economic_index_value_returns_decimal_on_200() -> None:
    """Returns a Decimal parsed from the index_value string."""
    respx.get(f"{BASE_URL}/economic-indices/value").mock(
        return_value=httpx.Response(200, json={"index_value": "36789.456789"})
    )
    value = await _client().get_economic_index_value("UF", 2026, 4)
    assert value == Decimal("36789.456789")


@pytest.mark.asyncio
@respx.mock
async def test_get_economic_index_value_returns_none_on_404() -> None:
    """Returns None on 404."""
    respx.get(f"{BASE_URL}/economic-indices/value").mock(
        return_value=httpx.Response(404)
    )
    value = await _client().get_economic_index_value("IPC_CL", 2026, 4)
    assert value is None


@pytest.mark.asyncio
@respx.mock
async def test_get_economic_index_value_caches_within_ttl() -> None:
    """Multiple calls within TTL produce exactly one HTTP request."""
    clock = [0.0, 1.0, 2.0]
    route = respx.get(f"{BASE_URL}/economic-indices/value").mock(
        return_value=httpx.Response(200, json={"index_value": "36000.00"})
    )
    client = _client(ttl=300, clock_values=clock)
    for _ in range(3):
        await client.get_economic_index_value("UF", 2026, 4)
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# get_latest_economic_index
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_latest_economic_index_returns_first_item_on_200() -> None:
    """Returns (period, value) parsed from the first (most recent) list item.

    GET /economic-indices?code=... already orders results by period
    descending server-side -- the client trusts that ordering rather than
    re-sorting client-side.
    """
    respx.get(f"{BASE_URL}/economic-indices").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "code": "IPC_CL",
                    "period_year": 2026,
                    "period_month": 8,
                    "index_value": "113.15",
                    "monthly_change": None,
                    "yearly_change": None,
                    "base_period": "DIC-2018",
                    "source": "manual",
                },
                {
                    "code": "IPC_CL",
                    "period_year": 2026,
                    "period_month": 7,
                    "index_value": "112.45",
                    "monthly_change": None,
                    "yearly_change": None,
                    "base_period": "DIC-2018",
                    "source": "manual",
                },
            ],
        )
    )
    result = await _client().get_latest_economic_index("IPC_CL")
    assert result == (date(2026, 8, 1), Decimal("113.15"))


@pytest.mark.asyncio
@respx.mock
async def test_get_latest_economic_index_returns_none_on_empty_list() -> None:
    """An empty list (200 OK, not 404) means the code has no stored values."""
    respx.get(f"{BASE_URL}/economic-indices").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = await _client().get_latest_economic_index("IPC_CL")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_get_latest_economic_index_returns_none_on_404() -> None:
    """Returns None on 404, same as the other pf-rates lookups."""
    respx.get(f"{BASE_URL}/economic-indices").mock(return_value=httpx.Response(404))
    result = await _client().get_latest_economic_index("IPC_CL")
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_get_latest_economic_index_caches_within_ttl() -> None:
    """Multiple calls within TTL produce exactly one HTTP request."""
    clock = [0.0, 1.0, 2.0]
    route = respx.get(f"{BASE_URL}/economic-indices").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "code": "IPC_CL",
                    "period_year": 2026,
                    "period_month": 8,
                    "index_value": "113.15",
                    "monthly_change": None,
                    "yearly_change": None,
                    "base_period": "DIC-2018",
                    "source": "manual",
                }
            ],
        )
    )
    client = _client(ttl=300, clock_values=clock)
    for _ in range(3):
        await client.get_latest_economic_index("IPC_CL")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_get_latest_economic_index_raises_on_5xx() -> None:
    """Raises PayrollDependencyError on server errors, same as other lookups."""
    respx.get(f"{BASE_URL}/economic-indices").mock(return_value=httpx.Response(502))
    with pytest.raises(PayrollDependencyError, match="pf-rates returned HTTP 502"):
        await _client().get_latest_economic_index("IPC_CL")
