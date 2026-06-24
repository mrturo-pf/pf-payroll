"""Tests for test deflate amounts."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    DeflateAmountsCommandDTO,
    PayrollPeriodDetailDTO,
)
from payroll.application.use_cases.deflate_amounts import DeflateAmounts
from payroll.domain.contributions import EmploymentContractKind
from tests.helpers.reference_data import sample_payroll_summary_dto


class StubPayrollRepository:
    """Test double for Payroll Repository."""

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO | None:
        """Get period detail."""
        if period_id == 404:
            return None
        return PayrollPeriodDetailDTO(
            id=period_id,
            employer_id=1,
            employer_name="ACME",
            employer_tax_id=None,
            employer_country_code="CL",
            employer_started_at=date(2020, 1, 1),
            employer_ended_at=None,
            period_year=2026,
            period_month=1,
            payment_date=date(2026, 1, 31),
            worked_days=30,
            status="actual",
            employment_contract_kind=EmploymentContractKind.INDEFINITE,
            pension_plan_id=1,
            health_plan_id=2,
            items=[],
            summary=sample_payroll_summary_dto(period_id),
        )


class StubMarketDataRepository:
    """Test double for Market Data Repository."""

    def __init__(
        self,
        source: Decimal | None = Decimal("100.000000"),
        target: Decimal | None = Decimal("112.340000"),
    ) -> None:
        """Initialize the instance."""
        self.source = source
        self.target = target
        self.calls: list[tuple[str, int, int]] = []

    async def get_economic_index_value(
        self, code: str, period_year: int, period_month: int
    ) -> Decimal | None:
        """Get economic index value."""
        self.calls.append((code, period_year, period_month))
        if (period_year, period_month) == (2026, 1):
            return self.source
        if (period_year, period_month) == (2026, 3):
            return self.target
        return None


@pytest.mark.asyncio
async def test_deflate_amounts_uses_ipc_and_returns_real_values() -> None:
    """Test deflate amounts uses ipc and returns real values."""
    result = await DeflateAmounts(
        StubPayrollRepository(), StubMarketDataRepository()
    ).execute(DeflateAmountsCommandDTO(period_id=1, target_year=2026, target_month=3))

    assert result.source_index_value == Decimal("100.000000")
    assert result.target_index_value == Decimal("112.340000")
    assert result.net_pay.real_clp == Decimal("932422")


@pytest.mark.asyncio
async def test_deflate_amounts_rejects_missing_summary() -> None:
    """Test deflate amounts rejects missing summary."""
    with pytest.raises(
        ValueError, match="Payroll summary for period 404 was not found."
    ):
        await DeflateAmounts(
            StubPayrollRepository(), StubMarketDataRepository()
        ).execute(
            DeflateAmountsCommandDTO(period_id=404, target_year=2026, target_month=3)
        )


@pytest.mark.asyncio
async def test_deflate_amounts_rejects_missing_indices() -> None:
    """Test deflate amounts rejects missing indices."""
    use_case = DeflateAmounts(
        StubPayrollRepository(), StubMarketDataRepository(target=None)
    )

    with pytest.raises(
        ValueError, match="Economic index IPC_CL for 2026-03 was not found."
    ):
        await use_case.execute(
            DeflateAmountsCommandDTO(period_id=1, target_year=2026, target_month=3)
        )


@pytest.mark.asyncio
async def test_deflate_amounts_rejects_missing_source_index() -> None:
    """Test deflate amounts rejects missing source index."""
    use_case = DeflateAmounts(
        StubPayrollRepository(), StubMarketDataRepository(source=None)
    )

    with pytest.raises(
        ValueError, match="Economic index IPC_CL for 2026-01 was not found."
    ):
        await use_case.execute(
            DeflateAmountsCommandDTO(period_id=1, target_year=2026, target_month=3)
        )
