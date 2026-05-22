"""Fallback chain for FX providers."""

from datetime import date
from decimal import Decimal

from payroll.application.ports.rate_provider import FxRateProvider


class ChainedFxProvider:
    name = "chained"

    def __init__(self, providers: list[FxRateProvider]) -> None:
        self._providers = providers

    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None:
        for provider in self._providers:
            value = await provider.fetch_rate(currency_code, on)
            if value is not None:
                return value
        return None
