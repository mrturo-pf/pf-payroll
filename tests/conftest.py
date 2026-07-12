"""Shared pytest fixtures."""

import os

# Force a predictable test key before any source module is imported so that
# Settings() (a required field, no default) loads successfully.  Forced
# assignment (not setdefault) ensures this works even when the host shell
# already exports a different PF_PAYROLL_API_KEY value.
os.environ["PF_PAYROLL_API_KEY"] = "test-key"

from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import PayrollPeriodDetailDTO, PayrollSummaryDTO


@pytest.fixture
def payroll_summary_dto() -> PayrollSummaryDTO:
    """Create a standard PayrollSummaryDTO for testing."""
    return PayrollSummaryDTO(
        period_id=1,
        employer_id=1,
        employer_name="Test Corp",
        period_year=2025,
        period_month=5,
        payment_date=date(2025, 5, 30),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1250000"),
        total_discounts_clp=Decimal("250000"),
        net_pay_clp=Decimal("1000000"),
    )


@pytest.fixture
def payroll_period_detail_dto(
    payroll_summary_dto: PayrollSummaryDTO,
) -> PayrollPeriodDetailDTO:
    """Create a standard PayrollPeriodDetailDTO for testing."""
    return PayrollPeriodDetailDTO(
        id=1,
        employer_id=1,
        employer_name="Test Corp",
        employer_tax_id="123456789",
        employer_country_code="CL",
        employer_started_at=date(2020, 1, 1),
        employer_ended_at=None,
        period_year=2025,
        period_month=5,
        payment_date=date(2025, 5, 30),
        status="actual",
        employment_contract_kind="indefinite",
        worked_days=30,
        summary=payroll_summary_dto,
        items=[],
        pension_plan_id=1,
        health_plan_id=2,
    )
