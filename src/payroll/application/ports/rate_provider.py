"""Port definitions for exchange-rate providers."""

from datetime import date
from decimal import Decimal
from typing import Protocol


class FxRateProvider(Protocol):
    async def fetch_rate(self, currency_code: str, on: date) -> Decimal | None: ...
