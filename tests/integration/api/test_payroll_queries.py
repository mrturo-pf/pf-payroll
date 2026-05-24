"""Tests for test payroll queries."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from payroll.application.errors import PayrollPeriodNotFoundError
from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.interfaces.api.dependencies import get_payroll_queries
from payroll.interfaces.api.main import app
from payroll.interfaces.api.routes.payroll import get_payroll_period
from payroll.domain.contributions import EmploymentContractKind


class FakePayrollQueries:
    """Test double for Payroll Queries."""

    async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO:
        """Get period detail."""
        assert period_id == 7
        return PayrollPeriodDetailDTO(
            id=7,
            employer_id=1,
            employer_name="ACME",
            employer_tax_id="76.123.456-7",
            employer_country_code="CL",
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
                ),
                PayrollItemDetailDTO(
                    concept_code="PENSION_BASE",
                    concept_name="Pension Base",
                    kind="discount",
                    is_taxable=False,
                    amount_clp=Decimal("100000"),
                    notes="computed",
                ),
            ],
            summary=PayrollSummaryDTO(
                period_id=7,
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
                period_id=7,
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


def test_payroll_query_endpoints() -> None:
    """Test payroll query endpoints."""
    app.dependency_overrides[get_payroll_queries] = lambda: FakePayrollQueries()
    client = TestClient(app)

    try:
        summary_response = client.get("/payroll/summary")
        detail_response = client.get("/payroll/7")
    finally:
        app.dependency_overrides.clear()

    assert summary_response.status_code == 200
    assert summary_response.json() == [
        {
            "period_id": 7,
            "employer_id": 1,
            "employer_name": "ACME",
            "period_year": 2026,
            "period_month": 1,
            "payment_date": "2026-01-31",
            "taxable_income_clp": "1000000",
            "gross_income_clp": "1000000",
            "total_discounts_clp": "170000",
            "net_pay_clp": "830000",
        }
    ]
    assert detail_response.status_code == 200
    assert detail_response.json() == {
        "id": 7,
        "employer_id": 1,
        "employer_name": "ACME",
        "employer_tax_id": "76.123.456-7",
        "employer_country_code": "CL",
        "period_year": 2026,
        "period_month": 1,
        "payment_date": "2026-01-31",
        "worked_days": 30,
        "status": "actual",
        "employment_contract_kind": "indefinite",
        "pension_plan_id": 1,
        "health_plan_id": 2,
        "items": [
            {
                "concept_code": "SALARY_BASE",
                "concept_name": "Base Salary",
                "kind": "income",
                "is_taxable": True,
                "amount_clp": "1000000",
                "notes": None,
            },
            {
                "concept_code": "PENSION_BASE",
                "concept_name": "Pension Base",
                "kind": "discount",
                "is_taxable": False,
                "amount_clp": "100000",
                "notes": "computed",
            },
        ],
        "summary": {
            "period_id": 7,
            "employer_id": 1,
            "employer_name": "ACME",
            "period_year": 2026,
            "period_month": 1,
            "payment_date": "2026-01-31",
            "taxable_income_clp": "1000000",
            "gross_income_clp": "1000000",
            "total_discounts_clp": "170000",
            "net_pay_clp": "830000",
        },
    }


def test_payroll_detail_endpoint_surfaces_not_found() -> None:
    """Test payroll detail endpoint surfaces not found."""

    class ErrorPayrollQueries:
        """Represent the error payroll queries."""

        async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO:
            """Get period detail."""
            raise PayrollPeriodNotFoundError("Payroll period 9 was not found.")

        async def list_period_summaries(self) -> list[PayrollSummaryDTO]:
            """List period summaries."""
            return []

    app.dependency_overrides[get_payroll_queries] = lambda: ErrorPayrollQueries()
    client = TestClient(app)

    try:
        response = client.get("/payroll/9")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "Payroll period 9 was not found."}


@pytest.mark.asyncio
async def test_payroll_detail_handler_maps_value_errors() -> None:
    """Test payroll detail handler maps value errors."""

    class ErrorPayrollQueries:
        """Represent the error payroll queries."""

        async def get_period_detail(self, period_id: int) -> PayrollPeriodDetailDTO:
            """Get period detail."""
            raise PayrollPeriodNotFoundError("Payroll period 9 was not found.")

    with pytest.raises(HTTPException, match="Payroll period 9 was not found."):
        await get_payroll_period(period_id=9, queries=ErrorPayrollQueries())
