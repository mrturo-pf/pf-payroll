"""Use case for refreshing rates and economic indices."""

from dataclasses import dataclass

from payroll.application.dto import (
    EconomicIndexWriteDTO,
    ExchangeRateWriteDTO,
    ProviderEconomicIndexRequestDTO,
    ProviderExchangeRateRequestDTO,
    RefreshRatesCommandDTO,
    RefreshRatesResultDTO,
)
from payroll.application.ports.rate_provider import EconomicIndexProvider, FxRateProvider
from payroll.application.ports.repositories import MarketDataRepository


@dataclass(slots=True)
class RefreshRates:
    """Stores historical exchange rates and economic indices."""

    repository: MarketDataRepository
    fx_provider: FxRateProvider | None = None
    economic_index_provider: EconomicIndexProvider | None = None

    async def execute(self, command: RefreshRatesCommandDTO) -> RefreshRatesResultDTO:
        if (
            not command.exchange_rates
            and not command.economic_indices
            and not command.provider_exchange_rates
            and not command.provider_economic_indices
        ):
            raise ValueError("At least one exchange rate or economic index entry is required.")

        exchange_rates: list[ExchangeRateWriteDTO] = [
            ExchangeRateWriteDTO(
                currency_code=item.currency_code.strip().upper(),
                rate_date=item.rate_date,
                value_clp=item.value_clp,
                source=item.source.strip() or "manual",
            )
            for item in command.exchange_rates
        ]
        economic_indices: list[EconomicIndexWriteDTO] = [
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
        ]
        provider_exchange_rates: list[ProviderExchangeRateRequestDTO] = [
            ProviderExchangeRateRequestDTO(
                currency_code=item.currency_code.strip().upper(),
                rate_date=item.rate_date,
            )
            for item in command.provider_exchange_rates
        ]
        provider_economic_indices: list[ProviderEconomicIndexRequestDTO] = [
            ProviderEconomicIndexRequestDTO(
                code=item.code.strip().upper(),
                period_year=item.period_year,
                period_month=item.period_month,
            )
            for item in command.provider_economic_indices
        ]

        if provider_exchange_rates and self.fx_provider is None:
            raise ValueError("Exchange-rate provider chain is not configured.")
        if provider_economic_indices and self.economic_index_provider is None:
            raise ValueError("Economic-index provider chain is not configured.")

        fx_provider = self.fx_provider
        economic_index_provider = self.economic_index_provider

        fetched_exchange_rates: list[ExchangeRateWriteDTO] = []
        for rate_request in provider_exchange_rates:
            assert fx_provider is not None
            rate_entry = await fx_provider.fetch_rate_entry(rate_request.currency_code, rate_request.rate_date)
            if rate_entry is None:
                raise ValueError(
                    f"Exchange rate {rate_request.currency_code} for {rate_request.rate_date.isoformat()} could not be fetched from configured providers."
                )
            fetched_exchange_rates.append(rate_entry)

        fetched_economic_indices: list[EconomicIndexWriteDTO] = []
        for index_request in provider_economic_indices:
            assert economic_index_provider is not None
            index_entry = await economic_index_provider.fetch_index(
                index_request.code,
                index_request.period_year,
                index_request.period_month,
            )
            if index_entry is None:
                raise ValueError(
                    f"Economic index {index_request.code} for {index_request.period_year:04d}-{index_request.period_month:02d} could not be fetched from configured providers."
                )
            fetched_economic_indices.append(index_entry)

        normalized_command = RefreshRatesCommandDTO(
            exchange_rates=list(
                {
                    (rate_item.currency_code, rate_item.rate_date): rate_item
                    for rate_item in [*fetched_exchange_rates, *exchange_rates]
                }.values()
            ),
            economic_indices=list(
                {
                    (index_item.code, index_item.period_year, index_item.period_month): index_item
                    for index_item in [*fetched_economic_indices, *economic_indices]
                }.values()
            ),
        )
        return await self.repository.refresh_rates(normalized_command)
