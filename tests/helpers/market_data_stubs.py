"""Shared market-data stub mixins for unit tests.

These mixins implement the repetitive UF-lookup pattern that appears across
multiple StubMarketDataRepository classes.  Inherit from the relevant mixin(s)
in per-file stubs to eliminate the boilerplate.
"""

from datetime import date
from decimal import Decimal


class UfLookupStubMixin:
    """Stub mixin: tracks UF exchange-rate lookups via uf_value + lookups.

    Stores a fixed or date-keyed UF value and records every
    get_exchange_rate_value call in self.lookups for assertion.
    """

    def __init__(
        self,
        uf_value: Decimal | dict[date, Decimal] | None = Decimal("35000"),
    ) -> None:
        """Initialise the mixin with a UF value and an empty lookup log."""
        self.uf_value = uf_value
        self.lookups: list[tuple[str, date]] = []

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> Decimal | None:
        """Return the configured UF value and record the lookup."""
        self.lookups.append((currency_code, rate_date))
        if isinstance(self.uf_value, dict):
            return self.uf_value.get(rate_date)
        return self.uf_value
