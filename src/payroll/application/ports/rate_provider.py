"""Port definitions for market-data providers."""

from datetime import date
from decimal import Decimal
from typing import Protocol

from payroll.application.dto import EconomicIndexWriteDTO, ExchangeRateWriteDTO, IncomeTaxBracketWriteDTO


class FxRateProvider(Protocol):
    """Provide fx rate provider."""

    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
        """Handle fetch rate."""
        ...

    async def fetch_rate_entry(self, currency_code: str, on: date) -> ExchangeRateWriteDTO | None:
        """Handle fetch rate entry."""
        ...


class EconomicIndexProvider(Protocol):
    """Provide economic index provider."""

    async def fetch_index(self, code: str, period_year: int, period_month: int) -> EconomicIndexWriteDTO | None:
        """Handle fetch index."""
        ...


class IncomeTaxBracketProvider(Protocol):
    """Provide income tax bracket provider."""

    async def fetch_income_tax_brackets(self, year: int) -> list[IncomeTaxBracketWriteDTO]:
        """Handle fetch income tax brackets."""
        ...
