"""Tests for test generate payroll report."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    GeneratedPayrollReportDTO,
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.application.use_cases.generate_payroll_report import GeneratePayrollReport
from payroll.domain.contributions import EmploymentContractKind


_DEFAULT_SUMMARY = object()


class StubPayrollRepository:
    """Test double for Payroll Repository."""

    def __init__(self, detail: PayrollPeriodDetailDTO | None) -> None:
        """Initialize the instance."""
        self.detail = detail

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO | None:
        """Get period detail."""
        assert period_id == 10
        return self.detail


class StubRenderer:
    """Test double for Renderer."""

    def __init__(self, content: bytes = b"%PDF-stub") -> None:
        """Initialize the instance."""
        self.content = content
        self.received: PayrollPeriodDetailDTO | None = None

    def render_payroll_period(self, detail: PayrollPeriodDetailDTO) -> bytes:
        """Render payroll period."""
        self.received = detail
        return self.content


def reviewed_detail(summary: PayrollSummaryDTO | None | object = _DEFAULT_SUMMARY) -> PayrollPeriodDetailDTO:
    """Handle reviewed detail."""
    return PayrollPeriodDetailDTO(
        id=10,
        employer_id=1,
        employer_name="ACME",
        employer_tax_id=None,
        employer_country_code="CL",
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        worked_days=30,
        status="reviewed",
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
        summary=(
            PayrollSummaryDTO(
                period_id=10,
                employer_id=1,
                employer_name="ACME",
                period_year=2026,
                period_month=1,
                payment_date=date(2026, 1, 31),
                taxable_income_clp=Decimal("1000000"),
                gross_income_clp=Decimal("1000000"),
                total_discounts_clp=Decimal("176000"),
                net_pay_clp=Decimal("824000"),
            )
            if summary is _DEFAULT_SUMMARY
            else summary
        ),
    )


@pytest.mark.asyncio
async def test_generate_payroll_report_returns_filename_and_pdf_bytes() -> None:
    """Test generate payroll report returns filename and pdf bytes."""
    renderer = StubRenderer()
    detail = reviewed_detail()

    result = await GeneratePayrollReport(StubPayrollRepository(detail), renderer).execute(10)

    assert result == GeneratedPayrollReportDTO(
        period_id=10,
        filename="payroll-period-10.pdf",
        content=b"%PDF-stub",
    )
    assert renderer.received == detail


@pytest.mark.asyncio
async def test_generate_payroll_report_rejects_missing_period() -> None:
    """Test generate payroll report rejects missing period."""
    with pytest.raises(ValueError, match="Payroll period 10 was not found."):
        await GeneratePayrollReport(StubPayrollRepository(None), StubRenderer()).execute(10)


@pytest.mark.asyncio
async def test_generate_payroll_report_requires_reviewed_status() -> None:
    """Test generate payroll report requires reviewed status."""
    detail = replace(reviewed_detail(), status="actual")

    with pytest.raises(ValueError, match="must be reviewed before generating a report"):
        await GeneratePayrollReport(StubPayrollRepository(detail), StubRenderer()).execute(10)


@pytest.mark.asyncio
async def test_generate_payroll_report_requires_summary() -> None:
    """Test generate payroll report requires summary."""
    with pytest.raises(ValueError, match="Payroll summary for period 10 was not found."):
        await GeneratePayrollReport(StubPayrollRepository(reviewed_detail(summary=None)), StubRenderer()).execute(10)
