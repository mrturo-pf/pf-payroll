from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    EconomicIndexWriteDTO,
    ExchangeRateWriteDTO,
    RefreshRatesCommandDTO,
    RefreshRatesResultDTO,
)
from payroll.application.use_cases.refresh_rates import RefreshRates


class StubMarketDataRepository:
    def __init__(self) -> None:
        self.command: RefreshRatesCommandDTO | None = None

    async def list_exchange_rates(self, currency_code: str | None = None) -> list[object]:
        raise AssertionError("not used")

    async def list_economic_indices(self, code: str | None = None) -> list[object]:
        raise AssertionError("not used")

    async def get_exchange_rate_value(self, currency_code: str, rate_date: date) -> Decimal | None:
        raise AssertionError("not used")

    async def refresh_rates(self, command: RefreshRatesCommandDTO) -> RefreshRatesResultDTO:
        self.command = command
        return RefreshRatesResultDTO(
            upserted_exchange_rates=len(command.exchange_rates),
            upserted_economic_indices=len(command.economic_indices),
        )


@pytest.mark.asyncio
async def test_refresh_rates_requires_non_empty_payload() -> None:
    with pytest.raises(ValueError, match="At least one exchange rate or economic index entry is required."):
        await RefreshRates(StubMarketDataRepository()).execute(
            RefreshRatesCommandDTO(exchange_rates=[], economic_indices=[])
        )


@pytest.mark.asyncio
async def test_refresh_rates_normalizes_codes_and_delegates() -> None:
    repository = StubMarketDataRepository()
    use_case = RefreshRates(repository)

    result = await use_case.execute(
        RefreshRatesCommandDTO(
            exchange_rates=[
                ExchangeRateWriteDTO(
                    currency_code=" uf ",
                    rate_date=date(2026, 1, 31),
                    value_clp=Decimal("38000"),
                    source="",
                )
            ],
            economic_indices=[
                EconomicIndexWriteDTO(
                    code=" ipc_cl ",
                    period_year=2026,
                    period_month=1,
                    index_value=Decimal("112.340000"),
                    monthly_change=Decimal("0.7000"),
                    yearly_change=Decimal("4.1000"),
                    base_period="",
                    source="",
                )
            ],
        )
    )

    assert result == RefreshRatesResultDTO(upserted_exchange_rates=1, upserted_economic_indices=1)
    assert repository.command is not None
    assert repository.command.exchange_rates[0].currency_code == "UF"
    assert repository.command.exchange_rates[0].source == "manual"
    assert repository.command.economic_indices[0].code == "IPC_CL"
    assert repository.command.economic_indices[0].base_period == "DIC-2018"
    assert repository.command.economic_indices[0].source == "manual"
