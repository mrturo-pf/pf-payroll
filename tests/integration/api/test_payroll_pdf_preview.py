"""Tests for the POST /payroll/import/pdf-preview endpoint."""

from decimal import Decimal
from datetime import date
from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from payroll.application.dto import PdfImportPreviewDTO, PdfImportPreviewRowDTO
from payroll.application.errors import PayrollValidationError
from payroll.domain.contributions import EmploymentContractKind
from payroll.interfaces.api.dependencies import get_preview_pdf_import_use_case
from payroll.interfaces.api.main import app
from payroll.interfaces.api.routes.payroll import preview_pdf_import


class FakePreviewPdfImport:
    """Test double for PreviewPdfImport."""

    async def execute(self, filename: str, content: bytes) -> PdfImportPreviewDTO:
        """Return a canned preview, asserting the upload was wired through."""
        assert filename == "payslip.pdf"
        assert content == b"%PDF-1.4 fake content"
        return PdfImportPreviewDTO(
            employer="ACME",
            period_year=2026,
            period_month=1,
            payment_date=date(2026, 1, 31),
            worked_days=30,
            declared_net_pay_clp=Decimal("950000"),
            employment_contract_kind=EmploymentContractKind.INDEFINITE,
            template_id="acme-v1",
            rows=[
                PdfImportPreviewRowDTO(
                    raw_label="SUELDO",
                    amount_clp=Decimal("1000000"),
                    kind="income",
                    concept_code="SALARY_BASE",
                    confidence=0.9,
                ),
                PdfImportPreviewRowDTO(
                    raw_label="BONO RARO",
                    amount_clp=Decimal("5000"),
                    kind="income",
                    concept_code=None,
                    confidence=0.0,
                ),
            ],
        )


class ErrorPreviewPdfImport:
    """Test double raising a validation error."""

    async def execute(self, filename: str, content: bytes) -> PdfImportPreviewDTO:
        """Raise to simulate an empty filename slipping through."""
        raise PayrollValidationError("A PDF file name is required.")


def test_preview_pdf_import_endpoint_returns_preview() -> None:
    """Test preview pdf import endpoint returns preview."""
    app.dependency_overrides[get_preview_pdf_import_use_case] = lambda: (
        FakePreviewPdfImport()
    )
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/pdf-preview",
            files={
                "file": ("payslip.pdf", b"%PDF-1.4 fake content", "application/pdf")
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "employer": "ACME",
        "period_year": 2026,
        "period_month": 1,
        "payment_date": "2026-01-31",
        "worked_days": 30,
        "declared_net_pay_clp": "950000",
        "employment_contract_kind": "indefinite",
        "template_id": "acme-v1",
        "rows": [
            {
                "raw_label": "SUELDO",
                "amount_clp": "1000000",
                "kind": "income",
                "concept_code": "SALARY_BASE",
                "confidence": 0.9,
            },
            {
                "raw_label": "BONO RARO",
                "amount_clp": "5000",
                "kind": "income",
                "concept_code": None,
                "confidence": 0.0,
            },
        ],
    }


def test_preview_pdf_import_endpoint_requires_filename() -> None:
    """An empty filename is rejected by FastAPI's own multipart validation."""
    app.dependency_overrides[get_preview_pdf_import_use_case] = lambda: (
        FakePreviewPdfImport()
    )
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/pdf-preview",
            files={"file": ("", b"noop", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_preview_pdf_import_endpoint_surfaces_validation_errors() -> None:
    """Test preview pdf import endpoint surfaces validation errors."""
    app.dependency_overrides[get_preview_pdf_import_use_case] = lambda: (
        ErrorPreviewPdfImport()
    )
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/import/pdf-preview",
            files={"file": ("payslip.pdf", b"noop", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_preview_pdf_import_rejects_empty_filename_in_handler() -> None:
    """Test preview pdf import rejects empty filename in handler.

    FastAPI's own File(...) validation already rejects an empty filename
    with 422 before the handler runs (see the 422 test above) -- this test
    exercises the handler's own defensive check directly, mirroring how
    import_payroll's equivalent branch is tested.
    """
    with pytest.raises(HTTPException, match="A PDF file name is required."):
        await preview_pdf_import(
            UploadFile(file=BytesIO(b"noop"), filename=""),
            FakePreviewPdfImport(),
        )
