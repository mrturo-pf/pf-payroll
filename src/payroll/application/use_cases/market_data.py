"""Read-only market-data queries."""

from dataclasses import dataclass

from payroll.application.dto import EconomicIndexDTO, ExchangeRateDTO
from payroll.application.ports.repositories import MarketDataRepository


@dataclass(slots=True)
class MarketDataQueries:
    """Provide market data queries."""

    repository: MarketDataRepository

    async def list_exchange_rates(self, currency_code: str | None = None) -> list[ExchangeRateDTO]:
        """List exchange rates."""
        normalized_code = currency_code.strip().upper() if currency_code is not None else None
        return await self.repository.list_exchange_rates(normalized_code)

    async def list_economic_indices(self, code: str | None = None) -> list[EconomicIndexDTO]:
        """List economic indices."""
        normalized_code = code.strip().upper() if code is not None else None
        return await self.repository.list_economic_indices(normalized_code)
