"""Tests for test payroll query use case."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollPeriodRangeContextDTO,
    PayrollPeriodRangeDTO,
    PayrollSummaryDTO,
)
from payroll.application.use_cases.payroll_queries import PayrollQueries
from helpers.reference_data import (
    PayrollPeriodRangesStubMixin,
    sample_payroll_period_detail_dto,
    sample_payroll_period_range_context_dto,
    sample_payroll_summary_dto,
)


class StubPayrollRepository(PayrollPeriodRangesStubMixin):
    """Test double for Payroll Repository."""

    last_previous_months: int | None = None
    last_future_months: int | None = None

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

    async def get_period_range(
        self, period_id: int
    ) -> PayrollPeriodRangeContextDTO | None:
        """Get a single period in the unified range shape."""
        if period_id == 404:
            return None
        target = PayrollPeriodRangeDTO(
            period_year=2026,
            period_month=1,
            start_date=date(2026, 1, 31),
            end_date=date(2026, 2, 27),
            net_pay_clp=Decimal("830000"),
            is_current=True,
            inferred=False,
            period_id=period_id,
            employer_id=1,
        )
        return sample_payroll_period_range_context_dto(target)

    def _build_period_ranges(
        self,
        *,
        today: date | None,
        previous_months: int | None,
        future_months: int | None,
    ) -> list[PayrollPeriodRangeDTO]:
        """Build the fixed single-period list list_period_ranges() exposes."""
        self.last_previous_months = previous_months
        self.last_future_months = future_months
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


@pytest.mark.asyncio
async def test_payroll_queries_forwards_previous_and_future_months() -> None:
    """previous_months/future_months must reach the repository unchanged."""
    repository = StubPayrollRepository()

    await PayrollQueries(repository).list_period_ranges(
        previous_months=6, future_months=3
    )

    assert repository.last_previous_months == 6
    assert repository.last_future_months == 3


@pytest.mark.asyncio
async def test_payroll_queries_return_period_range() -> None:
    """get_period_range() passes the repository's context DTO through."""
    context = await PayrollQueries(StubPayrollRepository()).get_period_range(7)

    assert context.target.period_id == 7
    assert context.target.net_pay_clp == Decimal("830000")


@pytest.mark.asyncio
async def test_payroll_queries_raise_for_missing_period_range() -> None:
    """get_period_range() raises PayrollPeriodNotFoundError for an unknown id."""
    with pytest.raises(ValueError, match="Payroll period 404 was not found."):
        await PayrollQueries(StubPayrollRepository()).get_period_range(404)
