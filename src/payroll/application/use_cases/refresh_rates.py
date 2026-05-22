"""Use case for refreshing rates and economic indices."""

from dataclasses import dataclass

from payroll.application.dto import (
    EconomicIndexWriteDTO,
    ExchangeRateWriteDTO,
    RefreshRatesCommandDTO,
    RefreshRatesResultDTO,
)
from payroll.application.ports.repositories import MarketDataRepository


@dataclass(slots=True)
class RefreshRates:
    """Stores historical exchange rates and economic indices."""

    repository: MarketDataRepository

    async def execute(self, command: RefreshRatesCommandDTO) -> RefreshRatesResultDTO:
        if not command.exchange_rates and not command.economic_indices:
            raise ValueError("At least one exchange rate or economic index entry is required.")

        normalized_command = RefreshRatesCommandDTO(
            exchange_rates=[
                ExchangeRateWriteDTO(
                    currency_code=item.currency_code.strip().upper(),
                    rate_date=item.rate_date,
                    value_clp=item.value_clp,
                    source=item.source.strip() or "manual",
                )
                for item in command.exchange_rates
            ],
            economic_indices=[
                EconomicIndexWriteDTO(
                    code=item.code.strip().upper(),
                    period_year=item.period_year,
                    period_month=item.period_month,
                    index_value=item.index_value,
                    monthly_change=item.monthly_change,
                    yearly_change=item.yearly_change,
                    base_period=item.base_period.strip() or "DIC-2018",
                    source=item.source.strip() or "manual",
                )
                for item in command.economic_indices
            ],
        )
        return await self.repository.refresh_rates(normalized_command)
