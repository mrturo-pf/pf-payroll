"""Tests for test market data queries."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import EconomicIndexDTO, ExchangeRateDTO
from payroll.application.use_cases.market_data import MarketDataQueries
from helpers.reference_data import (
    sample_economic_index_dto,
    sample_exchange_rate_dto,
)


class StubMarketDataRepository:
    """Test double for Market Data Repository."""

    async def list_exchange_rates(
        self, currency_code: str | None = None
    ) -> list[ExchangeRateDTO]:
        """List exchange rates."""
        assert currency_code == "UF"
        return [sample_exchange_rate_dto()]

    async def list_economic_indices(
        self, code: str | None = None
    ) -> list[EconomicIndexDTO]:
        """List economic indices."""
        assert code == "IPC_CL"
        return [sample_economic_index_dto()]

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> Decimal | None:
        """Get exchange rate value."""
        raise AssertionError("not used")

    async def refresh_rates(self, command: object) -> object:
        """Refresh rates."""
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_market_data_queries_delegate_to_repository_and_normalize_filters() -> (
    None
):
    """Test market data queries delegate to repository and normalize filters."""
    queries = MarketDataQueries(StubMarketDataRepository())

    exchange_rates = await queries.list_exchange_rates(" uf ")
    economic_indices = await queries.list_economic_indices(" ipc_cl ")

    assert [item.currency_code for item in exchange_rates] == ["UF"]
    assert [item.code for item in economic_indices] == ["IPC_CL"]
