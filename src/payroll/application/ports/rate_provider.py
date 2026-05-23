"""Port definitions for market-data providers."""

from datetime import date
from decimal import Decimal
from typing import Protocol

from payroll.application.dto import EconomicIndexWriteDTO, ExchangeRateWriteDTO, IncomeTaxBracketWriteDTO


class FxRateProvider(Protocol):
    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None: ...
    async def fetch_rate_entry(self, currency_code: str, on: date) -> ExchangeRateWriteDTO | None: ...


class EconomicIndexProvider(Protocol):
    async def fetch_index(self, code: str, period_year: int, period_month: int) -> EconomicIndexWriteDTO | None: ...


class IncomeTaxBracketProvider(Protocol):
    async def fetch_income_tax_brackets(self, year: int) -> list[IncomeTaxBracketWriteDTO]: ...
