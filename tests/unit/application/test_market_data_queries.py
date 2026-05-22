from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import EconomicIndexDTO, ExchangeRateDTO
from payroll.application.use_cases.market_data import MarketDataQueries


class StubMarketDataRepository:
    async def list_exchange_rates(self, currency_code: str | None = None) -> list[ExchangeRateDTO]:
        assert currency_code == "UF"
        return [ExchangeRateDTO(currency_code="UF", rate_date=date(2026, 1, 31), value_clp=Decimal("38000"), source="manual")]

    async def list_economic_indices(self, code: str | None = None) -> list[EconomicIndexDTO]:
        assert code == "IPC_CL"
        return [
            EconomicIndexDTO(
                code="IPC_CL",
                period_year=2026,
                period_month=1,
                index_value=Decimal("112.340000"),
                monthly_change=Decimal("0.7000"),
                yearly_change=Decimal("4.1000"),
                base_period="DIC-2018",
                source="manual",
            )
        ]

    async def get_exchange_rate_value(self, currency_code: str, rate_date: date) -> Decimal | None:
        raise AssertionError("not used")

    async def refresh_rates(self, command: object) -> object:
        raise AssertionError("not used")


@pytest.mark.asyncio
async def test_market_data_queries_delegate_to_repository_and_normalize_filters() -> None:
    queries = MarketDataQueries(StubMarketDataRepository())

    exchange_rates = await queries.list_exchange_rates(" uf ")
    economic_indices = await queries.list_economic_indices(" ipc_cl ")

    assert [item.currency_code for item in exchange_rates] == ["UF"]
    assert [item.code for item in economic_indices] == ["IPC_CL"]
