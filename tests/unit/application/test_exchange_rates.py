"""Tests for exchange-rate resolution helpers."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.services.exchange_rates import resolve_required_exchange_rate


class _UnusedRepository:
    """Repository that fails if an explicit value is not respected."""

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> None:
        """Fail if the helper performs an unnecessary lookup."""
        raise AssertionError(f"unexpected lookup for {currency_code} on {rate_date}")


@pytest.mark.asyncio
async def test_resolve_required_exchange_rate_uses_explicit_value() -> None:
    """An explicit rate bypasses market-data lookup."""
    result = await resolve_required_exchange_rate(
        provided_value=Decimal("40000"),
        currency_code="UF",
        rate_date=date(2026, 1, 31),
        market_data_repository=_UnusedRepository(),  # type: ignore[arg-type]
    )

    assert result == Decimal("40000")
