"""Tests for the payroll spreadsheet export routes.

GET /payroll/spreadsheet and GET /payroll/spreadsheet/template.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from payroll.application.dto import (
    ExportPayrollFiltersDTO,
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.application.errors import PayrollError
from payroll.domain.contributions import EmploymentContractKind
from payroll.infrastructure.importers.xlsx_importer import wide_columns
from payroll.interfaces.api.dependencies import get_payroll_repository
from payroll.interfaces.api.main import app
from tests.helpers.export_fakes import FakePayrollRepository


def _build_detail(period_id: int, employer: str = "ACME") -> PayrollPeriodDetailDTO:
    summary = PayrollSummaryDTO(
        period_id=period_id,
        employer_id=1,
        employer_name=employer,
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1000000"),
        total_discounts_clp=Decimal("0"),
        net_pay_clp=Decimal("1000000"),
        declared_net_pay_clp=Decimal("1000000"),
    )
    return PayrollPeriodDetailDTO(
        id=period_id,
        employer_id=1,
        employer_name=employer,
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
        pension_plan_id=None,
        health_plan_id=None,
        items=[
            PayrollItemDetailDTO(
                "SALARY_BASE", "Base", "income", True, Decimal("1000000"), None
            )
        ],
        summary=summary,
    )


def _client() -> TestClient:
    return TestClient(app, headers={"X-API-Key": "test-key"})


def test_get_spreadsheet_template_csv_needs_no_repository() -> None:
    """The blank template never touches PayrollRepository -- no override needed."""
    client = _client()

    response = client.get("/payroll/spreadsheet/template?format=csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="payroll-template.csv"'
    )
    assert response.text.splitlines()[0].split(",") == wide_columns()
    assert len(response.text.splitlines()) == 1


def test_get_spreadsheet_template_xlsx() -> None:
    """The XLSX template is a real workbook, distinct media type from CSV."""
    client = _client()

    response = client.get("/payroll/spreadsheet/template?format=xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="payroll-template.xlsx"'
    )
    assert response.content.startswith(b"PK")


def test_get_spreadsheet_template_defaults_to_csv_when_format_omitted() -> None:
    """Format defaults to csv, matching the design recommendation's Item 3."""
    client = _client()

    response = client.get("/payroll/spreadsheet/template")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")


def test_get_spreadsheet_template_rejects_unsupported_format() -> None:
    """An unsupported format value is a 422, via FastAPI's own Literal validation."""
    client = _client()

    response = client.get("/payroll/spreadsheet/template?format=pdf")

    assert response.status_code == 422


def test_get_spreadsheet_exports_periods_from_repository() -> None:
    """GET /payroll/spreadsheet serializes whatever PayrollRepository returns."""
    repository = FakePayrollRepository([_build_detail(1)])
    app.dependency_overrides[get_payroll_repository] = lambda: repository
    client = _client()

    try:
        response = client.get("/payroll/spreadsheet?format=csv")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="payroll-export.csv"'
    )
    lines = response.text.splitlines()
    assert len(lines) == 2  # header + one period
    assert "1000000" in lines[1]


def test_get_spreadsheet_forwards_filters_to_repository() -> None:
    """employer/period_year/period_month query params reach the repository unchanged."""
    repository = FakePayrollRepository([])
    app.dependency_overrides[get_payroll_repository] = lambda: repository
    client = _client()

    try:
        client.get(
            "/payroll/spreadsheet?format=csv"
            "&employer=ACME&period_year=2026&period_month=1"
        )
    finally:
        app.dependency_overrides.clear()

    assert repository.received_filters == ExportPayrollFiltersDTO(
        employer="ACME", period_year=2026, period_month=1
    )


def test_get_spreadsheet_with_no_filters_requests_everything() -> None:
    """Omitting every filter asks the repository for every persisted period."""
    repository = FakePayrollRepository([])
    app.dependency_overrides[get_payroll_repository] = lambda: repository
    client = _client()

    try:
        client.get("/payroll/spreadsheet?format=csv")
    finally:
        app.dependency_overrides.clear()

    assert repository.received_filters == ExportPayrollFiltersDTO()


def test_get_spreadsheet_with_no_matching_periods_returns_header_only_csv() -> None:
    """A filter matching nothing is a valid header-only export, not an error."""
    repository = FakePayrollRepository([])
    app.dependency_overrides[get_payroll_repository] = lambda: repository
    client = _client()

    try:
        response = client.get("/payroll/spreadsheet?format=csv&employer=NOBODY")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(response.text.splitlines()) == 1


def test_get_spreadsheet_xlsx_format() -> None:
    """format=xlsx returns a real workbook for the bulk export too."""
    repository = FakePayrollRepository([_build_detail(1)])
    app.dependency_overrides[get_payroll_repository] = lambda: repository
    client = _client()

    try:
        response = client.get("/payroll/spreadsheet?format=xlsx")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.content.startswith(b"PK")


def test_get_spreadsheet_does_not_collide_with_period_detail_route() -> None:
    """GET /payroll/spreadsheet must never be swallowed by GET /payroll/{period_id}.

    Registration order in interfaces/api/main.py matters here -- see that
    module's comment on why payroll_export_router is included before
    payroll_router.
    """
    repository = FakePayrollRepository([])
    app.dependency_overrides[get_payroll_repository] = lambda: repository
    client = _client()

    try:
        response = client.get("/payroll/spreadsheet?format=csv")
    finally:
        app.dependency_overrides.clear()

    # A period-detail 404 would come back as {"detail": "..."} JSON with
    # int-parsing failing first as a 422 -- either way, not a CSV 200.
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")


class FailingPayrollRepository:
    """Test double whose list_period_details() always raises a PayrollError."""

    async def list_period_details(
        self, filters: ExportPayrollFiltersDTO
    ) -> list[PayrollPeriodDetailDTO]:
        """Simulate a genuine application-level failure while exporting."""
        raise PayrollError("boom")


def test_get_spreadsheet_maps_payroll_errors_to_http_exceptions() -> None:
    """A PayrollError raised while exporting becomes the matching HTTP status."""
    app.dependency_overrides[get_payroll_repository] = lambda: (
        FailingPayrollRepository()
    )
    client = _client()

    try:
        response = client.get("/payroll/spreadsheet?format=csv")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json() == {"detail": "boom"}
