"""Tests for PreviewPdfImport."""

import pytest

from datetime import date

from payroll.application.dto import PdfImportPreviewDTO
from payroll.application.errors import PayrollValidationError
from payroll.application.use_cases.preview_pdf_import import PreviewPdfImport


class FakePdfPayrollExtractor:
    """Test double for PdfPayrollExtractor."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.calls: list[tuple[str, bytes]] = []

    def extract_preview(self, filename: str, content: bytes) -> PdfImportPreviewDTO:
        """Extract preview."""
        self.calls.append((filename, content))
        return PdfImportPreviewDTO(
            employer="ACME",
            period_year=2026,
            period_month=1,
            payment_date=date(2026, 1, 30),
            worked_days=30,
            declared_net_pay_clp=None,
            employment_contract_kind=None,
            template_id="acme-v1",
            rows=[],
        )


class TestPreviewPdfImport:
    """Tests for PreviewPdfImport."""

    @pytest.mark.asyncio
    async def test_delegates_to_extractor_port(self) -> None:
        """Test delegates to extractor port."""
        extractor = FakePdfPayrollExtractor()
        use_case = PreviewPdfImport(extractor)

        result = await use_case.execute("payslip.pdf", b"content")

        assert extractor.calls == [("payslip.pdf", b"content")]
        assert result.employer == "ACME"
        assert result.template_id == "acme-v1"

    @pytest.mark.asyncio
    async def test_raises_when_filename_is_empty(self) -> None:
        """Test raises when filename is empty."""
        use_case = PreviewPdfImport(FakePdfPayrollExtractor())

        with pytest.raises(PayrollValidationError):
            await use_case.execute("", b"content")
