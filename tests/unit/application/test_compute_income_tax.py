"""Tests for compute income tax use case."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    ComputeIncomeTaxCommandDTO,
    ComputeIncomeTaxResultDTO,
    IncomeTaxContextDTO,
)
from payroll.application.use_cases.compute_income_tax import ComputeIncomeTax
from payroll.domain.taxes import IncomeTaxBracket


class StubPayrollRepository:
    """Test double for PayrollRepository."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.saved: ComputeIncomeTaxResultDTO | None = None

    async def get_income_tax_context(
        self, command: ComputeIncomeTaxCommandDTO
    ) -> IncomeTaxContextDTO:
        """Get income tax context."""
        assert command.period_id == 10
        return IncomeTaxContextDTO(
            period_id=10,
            payment_date=date(2026, 1, 31),
            taxable_income_clp=Decimal("2500000"),
            deductible_amount_clp=Decimal("200000"),
        )

    async def save_computed_income_tax(
        self, result: ComputeIncomeTaxResultDTO
    ) -> ComputeIncomeTaxResultDTO:
        """Save computed income tax."""
        self.saved = result
        return result


class StubMarketDataRepository:
    """Test double for MarketDataRepository."""

    def __init__(self, utm_value: Decimal | None = Decimal("67000")) -> None:
        """Initialize the instance."""
        self.utm_value = utm_value
        self.lookups: list[tuple[str, date]] = []

    async def get_exchange_rate_value(
        self, currency_code: str, rate_date: date
    ) -> Decimal | None:
        """Get exchange rate value."""
        self.lookups.append((currency_code, rate_date))
        return self.utm_value


class StubIncomeTaxBracketPort:
    """Test double for IncomeTaxBracketPort."""

    _DEFAULT_BRACKET = IncomeTaxBracket(
        valid_from=date(2026, 1, 1),
        valid_to=None,
        lower_bound_utm=Decimal("30.0000"),
        upper_bound_utm=Decimal("50.0000"),
        marginal_rate=Decimal("0.080000"),
        rebate_utm=Decimal("1.7400"),
    )

    def __init__(self, bracket: IncomeTaxBracket | None = _DEFAULT_BRACKET) -> None:
        """Initialize the instance."""
        self.bracket = bracket
        self.lookups: list[tuple[date, Decimal]] = []

    async def get_income_tax_bracket(
        self, payment_date: date, taxable_base_utm: Decimal
    ) -> IncomeTaxBracket | None:
        """Get income tax bracket."""
        self.lookups.append((payment_date, taxable_base_utm))
        return self.bracket


@pytest.mark.asyncio
async def test_compute_income_tax_uses_stored_utm_and_persists_result() -> None:
    """Test compute income tax uses stored utm and persists result."""
    repository = StubPayrollRepository()
    market_data_repository = StubMarketDataRepository()
    bracket_port = StubIncomeTaxBracketPort()
    result = await ComputeIncomeTax(
        repository, market_data_repository, bracket_port
    ).execute(ComputeIncomeTaxCommandDTO(period_id=10))

    assert market_data_repository.lookups == [("UTM", date(2026, 1, 31))]
    assert result.tax.tax_clp == Decimal("67420")
    assert repository.saved == result


@pytest.mark.asyncio
async def test_compute_income_tax_rejects_missing_utm_value() -> None:
    """Test compute income tax rejects missing utm value."""
    with pytest.raises(
        ValueError, match="UTM exchange rate for 2026-01-31 was not found."
    ):
        await ComputeIncomeTax(
            StubPayrollRepository(),
            StubMarketDataRepository(None),
            StubIncomeTaxBracketPort(),
        ).execute(ComputeIncomeTaxCommandDTO(period_id=10))


@pytest.mark.asyncio
async def test_compute_income_tax_rejects_missing_bracket() -> None:
    """Test compute income tax rejects missing bracket."""
    with pytest.raises(ValueError, match="No income tax bracket was found"):
        await ComputeIncomeTax(
            StubPayrollRepository(),
            StubMarketDataRepository(),
            StubIncomeTaxBracketPort(bracket=None),
        ).execute(ComputeIncomeTaxCommandDTO(period_id=10))
