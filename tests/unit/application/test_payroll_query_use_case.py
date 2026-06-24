"""Tests for test payroll query use case."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollPeriodRangeDTO,
    PayrollSummaryDTO,
)
from payroll.application.use_cases.payroll_queries import PayrollQueries
from tests.helpers.reference_data import (
    sample_payroll_period_detail_dto,
    sample_payroll_summary_dto,
)


class StubPayrollRepository:
    """Test double for Payroll Repository."""

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO | None:
        """Get period detail."""
        if period_id == 404:
            return None
        return sample_payroll_period_detail_dto(
            period_id,
            items=[
                PayrollItemDetailDTO(
                    concept_code="SALARY_BASE",
                    concept_name="Base Salary",
                    kind="income",
                    is_taxable=True,
                    amount_clp=Decimal("1000000"),
                    notes=None,
                )
            ],
        )

    async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
        """List period summaries."""
        return [sample_payroll_summary_dto(1)]

    async def list_period_ranges(
        self, *, today: date | None = None
    ) -> list[PayrollPeriodRangeDTO]:
        """List period ranges."""
        return [
            PayrollPeriodRangeDTO(
                period_year=(today or date(2026, 1, 15)).year,
                period_month=(today or date(2026, 1, 15)).month,
                start_date=date(2026, 1, 31),
                end_date=date(2026, 2, 27),
                net_pay_clp=Decimal("830000"),
                is_current=True,
                inferred=False,
            )
        ]


@pytest.mark.asyncio
async def test_payroll_queries_return_detail_and_summary() -> None:
    """Test payroll queries return detail and summary."""
    queries = PayrollQueries(StubPayrollRepository())

    detail = await queries.get_period_detail(1)
    summaries = await queries.list_period_summaries()

    assert detail.employer_name == "ACME"
    assert detail.items[0].concept_code == "SALARY_BASE"
    assert summaries[0].net_pay_clp == Decimal("830000")


@pytest.mark.asyncio
async def test_payroll_queries_raise_for_missing_period() -> None:
    """Test payroll queries raise for missing period."""
    with pytest.raises(ValueError, match="Payroll period 404 was not found."):
        await PayrollQueries(StubPayrollRepository()).get_period_detail(404)


@pytest.mark.asyncio
async def test_payroll_queries_return_period_ranges() -> None:
    """Test payroll queries return payroll period date ranges."""
    result = await PayrollQueries(StubPayrollRepository()).list_period_ranges(
        today=date(2026, 1, 15)
    )

    assert result == [
        PayrollPeriodRangeDTO(
            period_year=2026,
            period_month=1,
            start_date=date(2026, 1, 31),
            end_date=date(2026, 2, 27),
            net_pay_clp=Decimal("830000"),
            is_current=True,
            inferred=False,
        )
    ]
