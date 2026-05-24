"""Tests for test payroll query use case."""

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.application.use_cases.payroll_queries import PayrollQueries
from payroll.domain.contributions import EmploymentContractKind


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
            summary=PayrollSummaryDTO(
                period_id=period_id,
                employer_id=1,
                employer_name="ACME",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                taxable_income_clp=Decimal("1000000"),
                gross_income_clp=Decimal("1000000"),
                total_discounts_clp=Decimal("170000"),
                net_pay_clp=Decimal("830000"),
            ),
        )

    async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
        """List period summaries."""
        return [
            PayrollSummaryDTO(
                period_id=1,
                employer_id=1,
                employer_name="ACME",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                taxable_income_clp=Decimal("1000000"),
                gross_income_clp=Decimal("1000000"),
                total_discounts_clp=Decimal("170000"),
                net_pay_clp=Decimal("830000"),
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
