"""Fallback chains for market-data providers."""

from datetime import date
from decimal import Decimal

import structlog

from payroll.application.dto import EconomicIndexWriteDTO, ExchangeRateWriteDTO
from payroll.application.ports.rate_provider import EconomicIndexProvider, FxRateProvider

log = structlog.get_logger(__name__)


class ChainedFxProvider:
    """Provide chained fx provider."""

    name = "chained"

    def __init__(self, providers: list[FxRateProvider]) -> None:
        """Initialize the instance."""
        self._providers = providers

    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
        """Handle fetch rate."""
        entry = await self.fetch_rate_entry(currency_code, on)
        return None if entry is None else entry.value_clp

    async def fetch_rate_entry(self, currency_code: str, on: date) -> ExchangeRateWriteDTO | None:
        """Handle fetch rate entry."""
        for provider in self._providers:
            try:
                value = await provider.fetch_rate(currency_code, on)
            except Exception as exc:
                log.warning("provider_failed", provider=getattr(provider, "name", provider.__class__.__name__.lower()), error=str(exc))
                continue
            if value is not None:
                return ExchangeRateWriteDTO(
                    currency_code=currency_code,
                    rate_date=on,
                    value_clp=value,
                    source=getattr(provider, "name", provider.__class__.__name__.lower()),
                )
        return None


class ChainedEconomicIndexProvider:
    """Provide chained economic index provider."""

    name = "chained"

    def __init__(self, providers: list[EconomicIndexProvider]) -> None:
        """Initialize the instance."""
        self._providers = providers

    async def fetch_index(self, code: str, period_year: int, period_month: int) -> EconomicIndexWriteDTO | None:
        """Handle fetch index."""
        for provider in self._providers:
            try:
                value = await provider.fetch_index(code, period_year, period_month)
            except Exception as exc:
                log.warning("provider_failed", provider=getattr(provider, "name", provider.__class__.__name__.lower()), error=str(exc))
                continue
            if value is not None:
                return value
        return None
