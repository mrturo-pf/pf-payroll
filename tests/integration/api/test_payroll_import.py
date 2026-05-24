"""Tests for test payroll import."""

from datetime import date
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from payroll.application.errors import (
    EconomicIndexNotFoundError,
    PayrollConflictError,
    PayrollPeriodNotFoundError,
    PayrollValidationError,
)
from payroll.application.dto import (
    GeneratedPayrollReportDTO,
    AssignPlansResultDTO,
    ComputeContributionsResultDTO,
    DeflateAmountsResultDTO,
    DeflatedAmountDTO,
    ComputeIncomeTaxResultDTO,
    ImportPayrollResultDTO,
    ImportedPayrollPeriodDTO,
    MarketDataSyncRequestDTO,
    ReviewPayrollPeriodResultDTO,
)

from payroll.domain.contributions import (
    EmploymentContractKind,
    HealthContribution,
    HealthInstitutionKind,
    PensionContribution,
    UnemploymentContribution,
)
from payroll.domain.taxes import IncomeTaxComputation
from payroll.interfaces.api.dependencies import (
    get_assign_plans_use_case,
    get_compute_contributions_use_case,
    get_deflate_amounts_use_case,
    get_compute_income_tax_use_case,
    get_generate_payroll_report_use_case,
    get_import_payroll_use_case,
    get_review_payroll_period_use_case,
)
from payroll.interfaces.api.main import app
from payroll.interfaces.api.routes.payroll import (
    assign_plans,
    compute_contributions,
    compute_income_tax,
    deflate_amounts,
    get_payroll_report,
    import_payroll,
    review_payroll_period,
)


class FakeImportPayroll:
    """Test double for Import Payroll."""

    async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
        """Create from bytes."""
        assert filename == "sample.csv"
        assert b"salary_base" in content
        return ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=1,
            periods=[
                ImportedPayrollPeriodDTO(
                    id=1,
                    employer="ACME",
                    period_year=2026,
                    period_month=1,
                    payment_date=date(2026, 1, 31),
                    status="projected",
                    employment_contract_kind=EmploymentContractKind.INDEFINITE,
                    item_count=1,
                    declared_net_pay_clp=Decimal("950000"),
                    expected_net_pay_clp=Decimal("900000"),
                    net_pay_difference_clp=Decimal("50000"),
                    net_pay_warning=(
                        "Declared net_pay does not match the imported concept "
                        "totals. Difference: 50000 CLP."
                    ),
                )
            ],
        )


class FakeImportPayrollWithSyncRequest:
    """Test double for Import Payroll returning pending market-data gaps."""

    async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
        """Create from bytes."""
        assert filename == "sample.csv"
        return ImportPayrollResultDTO(
            imported_periods=1,
            imported_items=1,
            periods=[],
            market_data_sync_request=MarketDataSyncRequestDTO(
                exchange_rate_dates={"UF": [date(2026, 1, 31)]}
            ),
        )


class FakeComputeContributions:
    """Test double for Compute Contributions."""

    async def execute(self, command: object) -> ComputeContributionsResultDTO:
        """Handle execute."""
        assert getattr(command, "period_id") == 5
        return ComputeContributionsResultDTO(
            period_id=5,
            pension_plan_id=1,
            health_plan_id=2,
            taxable_income_clp=Decimal("1000000"),
            total_discount_clp=Decimal("202200"),
            pension=PensionContribution(
                institution_code="AFP_UNO",
                taxable_clp=Decimal("1000000"),
                cap_clp=Decimal("3152100"),
                capped_base_clp=Decimal("1000000"),
                base_amount_clp=Decimal("100000"),
                additional_amount_clp=Decimal("12700"),
            ),
            health=HealthContribution(
                institution_code="FONASA",
                institution_kind=HealthInstitutionKind.FONASA,
                taxable_clp=Decimal("1000000"),
                cap_clp=Decimal("3152100"),
                capped_base_clp=Decimal("1000000"),
                base_amount_clp=Decimal("70000"),
                contracted_uf=Decimal("0"),
                contracted_clp=Decimal("0"),
                additional_amount_clp=Decimal("13500"),
            ),
            unemployment=UnemploymentContribution(
                contract_kind=EmploymentContractKind.INDEFINITE,
                taxable_clp=Decimal("1000000"),
                cap_clp=Decimal("3152100"),
                capped_base_clp=Decimal("1000000"),
                employee_rate=Decimal("0.006"),
                employee_amount_clp=Decimal("6000"),
                employer_rate=Decimal("0.024"),
                employer_amount_clp=Decimal("24000"),
            ),
        )


class FakeAssignPlans:
    """Test double for Assign Plans."""

    async def execute(self, command: object) -> AssignPlansResultDTO:
        """Handle execute."""
        assert getattr(command, "period_id") == 5
        return AssignPlansResultDTO(
            period_id=5,
            payment_date=date(2026, 1, 31),
            pension_plan_id=1,
            health_plan_id=2,
        )


class FakeComputeIncomeTax:
    """Test double for Compute Income Tax."""

    async def execute(self, command: object) -> ComputeIncomeTaxResultDTO:
        """Handle execute."""
        assert getattr(command, "period_id") == 5
        return ComputeIncomeTaxResultDTO(
            period_id=5,
            tax=IncomeTaxComputation(
                taxable_income_clp=Decimal("1000000"),
                deductible_amount_clp=Decimal("176000"),
                taxable_base_clp=Decimal("824000"),
                utm_value_clp=Decimal("67000"),
                taxable_base_utm=Decimal("12.298507"),
                bracket_lower_bound_utm=Decimal("0"),
                bracket_upper_bound_utm=Decimal("13.5"),
                marginal_rate=Decimal("0"),
                rebate_utm=Decimal("0"),
                tax_utm=Decimal("0"),
                tax_clp=Decimal("0"),
            ),
        )


class FakeReviewPayrollPeriod:
    """Test double for Review Payroll Period."""

    async def execute(self, command: object) -> ReviewPayrollPeriodResultDTO:
        """Handle execute."""
        assert getattr(command, "period_id") == 5
        return ReviewPayrollPeriodResultDTO(
            period_id=5,
            payment_date=date(2026, 1, 31),
            status="reviewed",
        )


class FakeGeneratePayrollReport:
    """Test double for Generate Payroll Report."""

    async def execute(self, period_id: int) -> GeneratedPayrollReportDTO:
        """Handle execute."""
        assert period_id == 5
        return GeneratedPayrollReportDTO(
            period_id=5,
            filename="payroll-period-5.pdf",
            content=b"%PDF-fake",
        )


class FakeDeflateAmounts:
    """Test double for Deflate Amounts."""

    async def execute(self, command: object) -> DeflateAmountsResultDTO:
        """Handle execute."""
        assert getattr(command, "period_id") == 5
        return DeflateAmountsResultDTO(
            period_id=5,
            index_code="IPC_CL",
            source_year=2026,
            source_month=1,
            target_year=2026,
            target_month=3,
            source_index_value=Decimal("100.000000"),
            target_index_value=Decimal("112.340000"),
            taxable_income=DeflatedAmountDTO(
                nominal_clp=Decimal("1000000"), real_clp=Decimal("1123400")
            ),
            gross_income=DeflatedAmountDTO(
                nominal_clp=Decimal("1000000"), real_clp=Decimal("1123400")
            ),
            total_discounts=DeflatedAmountDTO(
                nominal_clp=Decimal("170000"), real_clp=Decimal("190978")
            ),
            net_pay=DeflatedAmountDTO(
                nominal_clp=Decimal("830000"), real_clp=Decimal("932422")
            ),
        )


def test_payroll_import_endpoint() -> None:
    """Test payroll import endpoint."""
    app.dependency_overrides[get_import_payroll_use_case] = lambda: FakeImportPayroll()
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/import",
            files={
                "file": (
                    "sample.csv",
                    b"period,employer,payment_date,employment_contract_kind,salary_base\n"
                    b"Jan/2026,ACME,2026-01-31,indefinite,1000000\n",
                    "text/csv",
                )
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "imported_periods": 1,
        "imported_items": 1,
        "periods": [
            {
                "id": 1,
                "employer": "ACME",
                "period_year": 2026,
                "period_month": 1,
                "payment_date": "2026-01-31",
                "status": "projected",
                "employment_contract_kind": "indefinite",
                "item_count": 1,
                "declared_net_pay_clp": "950000",
                "expected_net_pay_clp": "900000",
                "net_pay_difference_clp": "50000",
                "net_pay_warning": (
                    "Declared net_pay does not match the imported concept "
                    "totals. Difference: 50000 CLP."
                ),
            }
        ],
    }


def test_payroll_import_endpoint_schedules_background_market_data_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test payroll import endpoint schedules a background market-data sync."""
    scheduled_requests: list[MarketDataSyncRequestDTO | None] = []
    app.dependency_overrides[get_import_payroll_use_case] = lambda: (
        FakeImportPayrollWithSyncRequest()
    )
    monkeypatch.setattr(
        "payroll.interfaces.api.routes.payroll.schedule_payroll_market_data_sync",
        lambda app, sync_request: scheduled_requests.append(sync_request),
    )
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/import",
            files={"file": ("sample.csv", b"noop", "text/csv")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert scheduled_requests == [
        MarketDataSyncRequestDTO(exchange_rate_dates={"UF": [date(2026, 1, 31)]})
    ]


def test_payroll_import_endpoint_requires_filename_and_surfaces_value_errors() -> None:
    """Test payroll import endpoint requires filename and surfaces value errors."""

    class ErrorImportPayroll:
        """Represent the error import payroll."""

        async def from_bytes(
            self, filename: str, content: bytes
        ) -> ImportPayrollResultDTO:
            """Create from bytes."""
            raise PayrollValidationError("bad payroll file")

    app.dependency_overrides[get_import_payroll_use_case] = lambda: ErrorImportPayroll()
    client = TestClient(app)

    try:
        missing_name = client.post(
            "/payroll/import", files={"file": ("", b"noop", "text/csv")}
        )
        invalid_file = client.post(
            "/payroll/import", files={"file": ("bad.csv", b"noop", "text/csv")}
        )
    finally:
        app.dependency_overrides.clear()

    assert missing_name.status_code == 422
    assert invalid_file.status_code == 400
    assert invalid_file.json() == {"detail": "bad payroll file"}


@pytest.mark.asyncio
async def test_payroll_import_endpoint_rejects_empty_filename_in_handler() -> None:
    """Test payroll import endpoint rejects empty filename in handler."""
    with pytest.raises(HTTPException, match="A payroll file name is required."):
        await import_payroll(
            SimpleNamespace(app=app),  # type: ignore[arg-type]
            UploadFile(file=BytesIO(b"noop"), filename=""),
            FakeImportPayroll(),
        )


def test_compute_contributions_endpoint() -> None:
    """Test compute contributions endpoint."""
    app.dependency_overrides[get_compute_contributions_use_case] = lambda: (
        FakeComputeContributions()
    )
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/5/compute-contributions",
            json={"pension_plan_id": 1, "health_plan_id": 2, "uf_value_clp": "35000"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "period_id": 5,
        "pension_plan_id": 1,
        "health_plan_id": 2,
        "taxable_income_clp": "1000000",
        "total_discount_clp": "202200",
        "pension": {
            "institution_code": "AFP_UNO",
            "taxable_clp": "1000000",
            "cap_clp": "3152100",
            "capped_base_clp": "1000000",
            "base_amount_clp": "100000",
            "additional_amount_clp": "12700",
        },
        "health": {
            "institution_code": "FONASA",
            "institution_kind": "fonasa",
            "taxable_clp": "1000000",
            "cap_clp": "3152100",
            "capped_base_clp": "1000000",
            "base_amount_clp": "70000",
            "contracted_uf": "0",
            "contracted_clp": "0",
            "additional_amount_clp": "13500",
        },
        "unemployment": {
            "contract_kind": "indefinite",
            "taxable_clp": "1000000",
            "cap_clp": "3152100",
            "capped_base_clp": "1000000",
            "employee_rate": "0.006",
            "employee_amount_clp": "6000",
            "employer_rate": "0.024",
            "employer_amount_clp": "24000",
        },
    }


def test_assign_plans_endpoint() -> None:
    """Test assign plans endpoint."""
    app.dependency_overrides[get_assign_plans_use_case] = lambda: FakeAssignPlans()
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/5/assign-plans", json={"pension_plan_id": 1, "health_plan_id": 2}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "period_id": 5,
        "payment_date": "2026-01-31",
        "pension_plan_id": 1,
        "health_plan_id": 2,
    }


def test_review_payroll_period_endpoint() -> None:
    """Test review payroll period endpoint."""
    app.dependency_overrides[get_review_payroll_period_use_case] = lambda: (
        FakeReviewPayrollPeriod()
    )
    client = TestClient(app)

    try:
        response = client.post("/payroll/5/review")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "period_id": 5,
        "payment_date": "2026-01-31",
        "status": "reviewed",
    }


def test_payroll_report_endpoint_returns_pdf() -> None:
    """Test payroll report endpoint returns pdf."""
    app.dependency_overrides[get_generate_payroll_report_use_case] = lambda: (
        FakeGeneratePayrollReport()
    )
    client = TestClient(app)

    try:
        response = client.get("/payroll/5/report.pdf")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="payroll-period-5.pdf"'
    )
    assert response.content == b"%PDF-fake"


def test_assign_plans_endpoint_surfaces_domain_errors() -> None:
    """Test assign plans endpoint surfaces domain errors."""

    class ErrorAssignPlans:
        """Represent the error assign plans."""

        async def execute(self, command: object) -> AssignPlansResultDTO:
            """Handle execute."""
            raise PayrollConflictError("invalid plan for period")

    app.dependency_overrides[get_assign_plans_use_case] = lambda: ErrorAssignPlans()
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/5/assign-plans", json={"pension_plan_id": 1, "health_plan_id": 2}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json() == {"detail": "invalid plan for period"}


def test_review_payroll_period_endpoint_surfaces_domain_errors() -> None:
    """Test review payroll period endpoint surfaces domain errors."""

    class ErrorReviewPayrollPeriod:
        """Represent the error review payroll period."""

        async def execute(self, command: object) -> ReviewPayrollPeriodResultDTO:
            """Handle execute."""
            raise PayrollConflictError("period must have computed items before review")

    app.dependency_overrides[get_review_payroll_period_use_case] = lambda: (
        ErrorReviewPayrollPeriod()
    )
    client = TestClient(app)

    try:
        response = client.post("/payroll/5/review")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json() == {
        "detail": "period must have computed items before review"
    }


def test_payroll_report_endpoint_surfaces_domain_errors() -> None:
    """Test payroll report endpoint surfaces domain errors."""

    class ErrorGeneratePayrollReport:
        """Represent the error generate payroll report."""

        async def execute(self, period_id: int) -> GeneratedPayrollReportDTO:
            """Handle execute."""
            raise PayrollConflictError(
                "period must be reviewed before generating a report"
            )

    app.dependency_overrides[get_generate_payroll_report_use_case] = lambda: (
        ErrorGeneratePayrollReport()
    )
    client = TestClient(app)

    try:
        response = client.get("/payroll/5/report.pdf")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json() == {
        "detail": "period must be reviewed before generating a report"
    }


def test_compute_contributions_endpoint_surfaces_domain_errors() -> None:
    """Test compute contributions endpoint surfaces domain errors."""

    class ErrorComputeContributions:
        """Represent the error compute contributions."""

        async def execute(self, command: object) -> ComputeContributionsResultDTO:
            """Handle execute."""
            raise PayrollPeriodNotFoundError("period not found")

    app.dependency_overrides[get_compute_contributions_use_case] = lambda: (
        ErrorComputeContributions()
    )
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/5/compute-contributions",
            json={"pension_plan_id": 1, "health_plan_id": 2, "uf_value_clp": "35000"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "period not found"}


def test_compute_income_tax_endpoint() -> None:
    """Test compute income tax endpoint."""
    app.dependency_overrides[get_compute_income_tax_use_case] = lambda: (
        FakeComputeIncomeTax()
    )
    client = TestClient(app)

    try:
        response = client.post("/payroll/5/compute-tax", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "period_id": 5,
        "taxable_income_clp": "1000000",
        "deductible_amount_clp": "176000",
        "taxable_base_clp": "824000",
        "utm_value_clp": "67000",
        "taxable_base_utm": "12.298507",
        "bracket_lower_bound_utm": "0",
        "bracket_upper_bound_utm": "13.5",
        "marginal_rate": "0",
        "rebate_utm": "0",
        "tax_utm": "0",
        "tax_clp": "0",
    }


def test_compute_income_tax_endpoint_surfaces_domain_errors() -> None:
    """Test compute income tax endpoint surfaces domain errors."""

    class ErrorComputeIncomeTax:
        """Represent the error compute income tax."""

        async def execute(self, command: object) -> ComputeIncomeTaxResultDTO:
            """Handle execute."""
            raise PayrollPeriodNotFoundError("tax data not found")

    app.dependency_overrides[get_compute_income_tax_use_case] = lambda: (
        ErrorComputeIncomeTax()
    )
    client = TestClient(app)

    try:
        response = client.post("/payroll/5/compute-tax", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "tax data not found"}


def test_deflate_amounts_endpoint() -> None:
    """Test deflate amounts endpoint."""
    app.dependency_overrides[get_deflate_amounts_use_case] = lambda: (
        FakeDeflateAmounts()
    )
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/5/deflate", json={"target_year": 2026, "target_month": 3}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "period_id": 5,
        "index_code": "IPC_CL",
        "source_year": 2026,
        "source_month": 1,
        "target_year": 2026,
        "target_month": 3,
        "source_index_value": "100.000000",
        "target_index_value": "112.340000",
        "taxable_income": {"nominal_clp": "1000000", "real_clp": "1123400"},
        "gross_income": {"nominal_clp": "1000000", "real_clp": "1123400"},
        "total_discounts": {"nominal_clp": "170000", "real_clp": "190978"},
        "net_pay": {"nominal_clp": "830000", "real_clp": "932422"},
    }


def test_deflate_amounts_endpoint_surfaces_domain_errors() -> None:
    """Test deflate amounts endpoint surfaces domain errors."""

    class ErrorDeflateAmounts:
        """Represent the error deflate amounts."""

        async def execute(self, command: object) -> DeflateAmountsResultDTO:
            """Handle execute."""
            raise EconomicIndexNotFoundError("missing IPC data")

    app.dependency_overrides[get_deflate_amounts_use_case] = lambda: (
        ErrorDeflateAmounts()
    )
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/5/deflate", json={"target_year": 2026, "target_month": 3}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json() == {"detail": "missing IPC data"}


@pytest.mark.asyncio
async def test_compute_income_tax_endpoint_maps_value_errors_in_handler() -> None:
    """Test compute income tax endpoint maps value errors in handler."""

    class ErrorComputeIncomeTax:
        """Represent the error compute income tax."""

        async def execute(self, command: object) -> ComputeIncomeTaxResultDTO:
            """Handle execute."""
            raise PayrollValidationError("bad tax payload")

    with pytest.raises(HTTPException, match="bad tax payload"):
        await compute_income_tax(
            payload=type("Payload", (), {"utm_value_clp": Decimal("1")})(),
            period_id=1,
            use_case=ErrorComputeIncomeTax(),
        )


@pytest.mark.asyncio
async def test_deflate_amounts_endpoint_maps_value_errors_in_handler() -> None:
    """Test deflate amounts endpoint maps value errors in handler."""

    class ErrorDeflateAmounts:
        """Represent the error deflate amounts."""

        async def execute(self, command: object) -> DeflateAmountsResultDTO:
            """Handle execute."""
            raise PayrollValidationError("bad deflation payload")

    with pytest.raises(HTTPException, match="bad deflation payload"):
        await deflate_amounts(
            payload=type(
                "Payload",
                (),
                {"target_year": 2026, "target_month": 3, "index_code": "IPC_CL"},
            )(),
            period_id=1,
            use_case=ErrorDeflateAmounts(),
        )


@pytest.mark.asyncio
async def test_assign_plans_endpoint_maps_value_errors_in_handler() -> None:
    """Test assign plans endpoint maps value errors in handler."""

    class ErrorAssignPlans:
        """Represent the error assign plans."""

        async def execute(self, command: object) -> AssignPlansResultDTO:
            """Handle execute."""
            raise PayrollValidationError("bad plan assignment")

    with pytest.raises(HTTPException, match="bad plan assignment"):
        await assign_plans(
            payload=type("Payload", (), {"pension_plan_id": 1, "health_plan_id": 2})(),
            period_id=1,
            use_case=ErrorAssignPlans(),
        )


@pytest.mark.asyncio
async def test_review_payroll_period_endpoint_maps_value_errors_in_handler() -> None:
    """Test review payroll period endpoint maps value errors in handler."""

    class ErrorReviewPayrollPeriod:
        """Represent the error review payroll period."""

        async def execute(self, command: object) -> ReviewPayrollPeriodResultDTO:
            """Handle execute."""
            raise PayrollValidationError("bad review payload")

    with pytest.raises(HTTPException, match="bad review payload"):
        await review_payroll_period(
            period_id=1,
            use_case=ErrorReviewPayrollPeriod(),
        )


@pytest.mark.asyncio
async def test_payroll_report_endpoint_maps_value_errors_in_handler() -> None:
    """Test payroll report endpoint maps value errors in handler."""

    class ErrorGeneratePayrollReport:
        """Represent the error generate payroll report."""

        async def execute(self, period_id: int) -> GeneratedPayrollReportDTO:
            """Handle execute."""
            raise PayrollValidationError("bad report payload")

    with pytest.raises(HTTPException, match="bad report payload"):
        await get_payroll_report(
            period_id=1,
            use_case=ErrorGeneratePayrollReport(),
        )


@pytest.mark.asyncio
async def test_compute_contributions_endpoint_maps_value_errors_in_handler() -> None:
    """Test compute contributions endpoint maps value errors in handler."""

    class ErrorComputeContributions:
        """Represent the error compute contributions."""

        async def execute(self, command: object) -> ComputeContributionsResultDTO:
            """Handle execute."""
            raise PayrollValidationError("bad payload")

    with pytest.raises(HTTPException, match="bad payload"):
        await compute_contributions(
            payload=type(
                "Payload",
                (),
                {
                    "pension_plan_id": 1,
                    "health_plan_id": 2,
                    "uf_value_clp": Decimal("1"),
                },
            )(),
            period_id=1,
            use_case=ErrorComputeContributions(),
        )
