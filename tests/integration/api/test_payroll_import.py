from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from payroll.application.dto import (
    ComputeContributionsResultDTO,
    ComputeIncomeTaxResultDTO,
    ImportPayrollResultDTO,
    ImportedPayrollPeriodDTO,
)
from payroll.domain.contributions import HealthContribution, HealthInstitutionKind, PensionContribution
from payroll.domain.taxes import IncomeTaxComputation
from payroll.interfaces.api.dependencies import (
    get_compute_contributions_use_case,
    get_compute_income_tax_use_case,
    get_import_payroll_use_case,
)
from payroll.interfaces.api.main import app
from payroll.interfaces.api.routes.payroll import compute_contributions, compute_income_tax, import_payroll


class FakeImportPayroll:
    async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
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
                    item_count=1,
                )
            ],
        )


class FakeComputeContributions:
    async def execute(self, command: object) -> ComputeContributionsResultDTO:
        assert getattr(command, "period_id") == 5
        return ComputeContributionsResultDTO(
            period_id=5,
            pension_plan_id=1,
            health_plan_id=2,
            taxable_income_clp=Decimal("1000000"),
            total_discount_clp=Decimal("196200"),
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
        )


class FakeComputeIncomeTax:
    async def execute(self, command: object) -> ComputeIncomeTaxResultDTO:
        assert getattr(command, "period_id") == 5
        return ComputeIncomeTaxResultDTO(
            period_id=5,
            tax=IncomeTaxComputation(
                taxable_income_clp=Decimal("1000000"),
                deductible_amount_clp=Decimal("170000"),
                taxable_base_clp=Decimal("830000"),
                utm_value_clp=Decimal("67000"),
                taxable_base_utm=Decimal("12.388060"),
                bracket_lower_bound_utm=Decimal("0"),
                bracket_upper_bound_utm=Decimal("13.5"),
                marginal_rate=Decimal("0"),
                rebate_utm=Decimal("0"),
                tax_utm=Decimal("0"),
                tax_clp=Decimal("0"),
            ),
        )


def test_payroll_import_endpoint() -> None:
    app.dependency_overrides[get_import_payroll_use_case] = lambda: FakeImportPayroll()
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/import",
            files={"file": ("sample.csv", b"period,employer,payment_date,salary_base\nJan/2026,ACME,2026-01-31,1000000\n", "text/csv")},
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
                "item_count": 1,
            }
        ],
    }


def test_payroll_import_endpoint_requires_filename_and_surfaces_value_errors() -> None:
    class ErrorImportPayroll:
        async def from_bytes(self, filename: str, content: bytes) -> ImportPayrollResultDTO:
            raise ValueError("bad payroll file")

    app.dependency_overrides[get_import_payroll_use_case] = lambda: ErrorImportPayroll()
    client = TestClient(app)

    try:
        missing_name = client.post("/payroll/import", files={"file": ("", b"noop", "text/csv")})
        invalid_file = client.post("/payroll/import", files={"file": ("bad.csv", b"noop", "text/csv")})
    finally:
        app.dependency_overrides.clear()

    assert missing_name.status_code == 422
    assert invalid_file.status_code == 400
    assert invalid_file.json() == {"detail": "bad payroll file"}


@pytest.mark.asyncio
async def test_payroll_import_endpoint_rejects_empty_filename_in_handler() -> None:
    with pytest.raises(HTTPException, match="A payroll file name is required."):
        await import_payroll(UploadFile(file=BytesIO(b"noop"), filename=""), FakeImportPayroll())


def test_compute_contributions_endpoint() -> None:
    app.dependency_overrides[get_compute_contributions_use_case] = lambda: FakeComputeContributions()
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
        "total_discount_clp": "196200",
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
    }


def test_compute_contributions_endpoint_surfaces_domain_errors() -> None:
    class ErrorComputeContributions:
        async def execute(self, command: object) -> ComputeContributionsResultDTO:
            raise ValueError("period not found")

    app.dependency_overrides[get_compute_contributions_use_case] = lambda: ErrorComputeContributions()
    client = TestClient(app)

    try:
        response = client.post(
            "/payroll/5/compute-contributions",
            json={"pension_plan_id": 1, "health_plan_id": 2, "uf_value_clp": "35000"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json() == {"detail": "period not found"}


def test_compute_income_tax_endpoint() -> None:
    app.dependency_overrides[get_compute_income_tax_use_case] = lambda: FakeComputeIncomeTax()
    client = TestClient(app)

    try:
        response = client.post("/payroll/5/compute-tax", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "period_id": 5,
        "taxable_income_clp": "1000000",
        "deductible_amount_clp": "170000",
        "taxable_base_clp": "830000",
        "utm_value_clp": "67000",
        "taxable_base_utm": "12.388060",
        "bracket_lower_bound_utm": "0",
        "bracket_upper_bound_utm": "13.5",
        "marginal_rate": "0",
        "rebate_utm": "0",
        "tax_utm": "0",
        "tax_clp": "0",
    }


def test_compute_income_tax_endpoint_surfaces_domain_errors() -> None:
    class ErrorComputeIncomeTax:
        async def execute(self, command: object) -> ComputeIncomeTaxResultDTO:
            raise ValueError("tax data not found")

    app.dependency_overrides[get_compute_income_tax_use_case] = lambda: ErrorComputeIncomeTax()
    client = TestClient(app)

    try:
        response = client.post("/payroll/5/compute-tax", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json() == {"detail": "tax data not found"}


@pytest.mark.asyncio
async def test_compute_income_tax_endpoint_maps_value_errors_in_handler() -> None:
    class ErrorComputeIncomeTax:
        async def execute(self, command: object) -> ComputeIncomeTaxResultDTO:
            raise ValueError("bad tax payload")

    with pytest.raises(HTTPException, match="bad tax payload"):
        await compute_income_tax(
            payload=type("Payload", (), {"utm_value_clp": Decimal("1")})(),
            period_id=1,
            use_case=ErrorComputeIncomeTax(),
        )


@pytest.mark.asyncio
async def test_compute_contributions_endpoint_maps_value_errors_in_handler() -> None:
    class ErrorComputeContributions:
        async def execute(self, command: object) -> ComputeContributionsResultDTO:
            raise ValueError("bad payload")

    with pytest.raises(HTTPException, match="bad payload"):
        await compute_contributions(
            payload=type("Payload", (), {"pension_plan_id": 1, "health_plan_id": 2, "uf_value_clp": Decimal("1")})(),
            period_id=1,
            use_case=ErrorComputeContributions(),
        )
