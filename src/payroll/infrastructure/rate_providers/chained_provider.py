"""Fallback chains for market-data providers."""

from datetime import date
from decimal import Decimal

import structlog

from payroll.application.dto import EconomicIndexWriteDTO, ExchangeRateWriteDTO
from payroll.application.ports.rate_provider import (
    EconomicIndexProvider,
    FxRateProvider,
)

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

    async def fetch_rate_entry(
        self, currency_code: str, on: date
    ) -> ExchangeRateWriteDTO | None:
        """Handle fetch rate entry."""
        for provider in self._providers:
            try:
                value = await provider.fetch_rate(currency_code, on)
            except Exception as exc:
                log.warning(
                    "provider_failed",
                    provider=getattr(
                        provider, "name", provider.__class__.__name__.lower()
                    ),
                    error=str(exc),
                )
                continue
            if value is not None:
                return ExchangeRateWriteDTO(
                    currency_code=currency_code,
                    rate_date=on,
                    value_clp=value,
                    source=getattr(
                        provider, "name", provider.__class__.__name__.lower()
                    ),
                )
        return None

    async def fetch_rate_entries(
        self, currency_code: str, requested_dates: list[date]
    ) -> list[ExchangeRateWriteDTO]:
        """Handle fetch rate entries."""
        remaining_dates = list(dict.fromkeys(requested_dates))
        entries_by_date: dict[date, ExchangeRateWriteDTO] = {}

        for provider in self._providers:
            if not remaining_dates:
                break
            try:
                entries = await provider.fetch_rate_entries(
                    currency_code, remaining_dates
                )
            except Exception as exc:
                log.warning(
                    "provider_failed",
                    provider=getattr(
                        provider, "name", provider.__class__.__name__.lower()
                    ),
                    error=str(exc),
                )
                continue
            provided_dates = {entry.rate_date for entry in entries}
            entries_by_date.update({entry.rate_date: entry for entry in entries})
            remaining_dates = [
                requested_date
                for requested_date in remaining_dates
                if requested_date not in provided_dates
            ]

        return [
            entries_by_date[requested_date]
            for requested_date in requested_dates
            if requested_date in entries_by_date
        ]


class ChainedEconomicIndexProvider:
    """Provide chained economic index provider."""

    name = "chained"

    def __init__(self, providers: list[EconomicIndexProvider]) -> None:
        """Initialize the instance."""
        self._providers = providers

    async def fetch_index(
        self, code: str, period_year: int, period_month: int
    ) -> EconomicIndexWriteDTO | None:
        """Handle fetch index."""
        for provider in self._providers:
            try:
                value = await provider.fetch_index(code, period_year, period_month)
            except Exception as exc:
                log.warning(
                    "provider_failed",
                    provider=getattr(
                        provider, "name", provider.__class__.__name__.lower()
                    ),
                    error=str(exc),
                )
                continue
            if value is not None:
                return value
        return None

    async def fetch_indices(
        self, code: str, requested_periods: list[tuple[int, int]]
    ) -> list[EconomicIndexWriteDTO]:
        """Handle fetch indices."""
        remaining_periods = list(dict.fromkeys(requested_periods))
        entries_by_period: dict[tuple[int, int], EconomicIndexWriteDTO] = {}

        for provider in self._providers:
            if not remaining_periods:
                break
            try:
                entries = await provider.fetch_indices(code, remaining_periods)
            except Exception as exc:
                log.warning(
                    "provider_failed",
                    provider=getattr(
                        provider, "name", provider.__class__.__name__.lower()
                    ),
                    error=str(exc),
                )
                continue
            provided_periods = {
                (entry.period_year, entry.period_month) for entry in entries
            }
            entries_by_period.update(
                {(entry.period_year, entry.period_month): entry for entry in entries}
            )
            remaining_periods = [
                requested_period
                for requested_period in remaining_periods
                if requested_period not in provided_periods
            ]

        return [
            entries_by_period[requested_period]
            for requested_period in requested_periods
            if requested_period in entries_by_period
        ]
