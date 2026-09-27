"""Tests for the POST /payroll/pdf-preview endpoint."""

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
    """Test double for PreviewPdfImport -- keys its response off the filename.

    Rather than returning one hardcoded DTO regardless of input, the
    employer field echoes the filename it was called with. That lets a
    multi-file test assert each array entry in the response corresponds to
    its own upload, in upload order, without files ever being mixed up.
    """

    async def execute(self, filename: str, content: bytes) -> PdfImportPreviewDTO:
        """Return a canned preview, asserting the upload content was wired through."""
        assert content.startswith(b"%PDF-1.4")
        return PdfImportPreviewDTO(
            employer=f"ACME ({filename})",
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


def _expected_preview(filename: str) -> dict:
    """Build the expected JSON body for one FakePreviewPdfImport() result."""
    return {
        "employer": f"ACME ({filename})",
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


def test_preview_pdf_import_endpoint_returns_preview_for_one_file() -> None:
    """A single-file request still works, wrapped in a one-element array."""
    app.dependency_overrides[get_preview_pdf_import_use_case] = lambda: (
        FakePreviewPdfImport()
    )
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/pdf-preview",
            files=[
                (
                    "files",
                    ("payslip.pdf", b"%PDF-1.4 fake content", "application/pdf"),
                )
            ],
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [_expected_preview("payslip.pdf")]


def test_preview_pdf_import_endpoint_returns_preview_per_file_in_upload_order() -> None:
    """Each uploaded PDF gets its own independent preview, in upload order.

    The batch use case is what makes POST /payroll/pdf-preview genuinely
    useful for a stack of distinct liquidaciones (different employees or
    periods): this asserts the response array's order matches the request's
    upload order, and that a second file's preview is never contaminated by
    the first's.
    """
    app.dependency_overrides[get_preview_pdf_import_use_case] = lambda: (
        FakePreviewPdfImport()
    )
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/pdf-preview",
            files=[
                (
                    "files",
                    ("payslip-a.pdf", b"%PDF-1.4 first payslip", "application/pdf"),
                ),
                (
                    "files",
                    ("payslip-b.pdf", b"%PDF-1.4 second payslip", "application/pdf"),
                ),
            ],
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        _expected_preview("payslip-a.pdf"),
        _expected_preview("payslip-b.pdf"),
    ]


def test_preview_pdf_import_endpoint_requires_filename() -> None:
    """An empty filename is rejected by FastAPI's own multipart validation."""
    app.dependency_overrides[get_preview_pdf_import_use_case] = lambda: (
        FakePreviewPdfImport()
    )
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/pdf-preview",
            files=[("files", ("", b"noop", "application/pdf"))],
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_preview_pdf_import_endpoint_fails_whole_batch_on_one_bad_file() -> None:
    """One file with no filename fails the entire batch, not just that entry.

    Mirrors the single-file behavior: a multi-file request never returns a
    partial array silently dropping the offending upload.
    """
    app.dependency_overrides[get_preview_pdf_import_use_case] = lambda: (
        FakePreviewPdfImport()
    )
    client = TestClient(app, headers={"X-API-Key": "test-key"})

    try:
        response = client.post(
            "/payroll/pdf-preview",
            files=[
                ("files", ("payslip-a.pdf", b"%PDF-1.4 ok", "application/pdf")),
                ("files", ("", b"noop", "application/pdf")),
            ],
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
            "/payroll/pdf-preview",
            files=[("files", ("payslip.pdf", b"noop", "application/pdf"))],
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
            [UploadFile(file=BytesIO(b"noop"), filename="")],
            FakePreviewPdfImport(),
        )
