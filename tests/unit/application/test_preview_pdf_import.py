"""Tests for PreviewPdfImport."""

import pytest

from datetime import date

from payroll.application.dto import EmployerPaymentRuleDTO, PdfImportPreviewDTO
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


class FakeEmployerPaymentRuleReader:
    """Test double for EmployerPaymentRuleReader."""

    def __init__(self, rule: EmployerPaymentRuleDTO | None) -> None:
        """Initialize the instance."""
        self._rule = rule
        self.requested_names: list[str] = []

    async def get_employer_payment_rule(
        self, employer_name: str
    ) -> EmployerPaymentRuleDTO | None:
        """Get employer payment rule."""
        self.requested_names.append(employer_name)
        return self._rule


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

    @pytest.mark.asyncio
    async def test_keeps_generic_guess_without_reference_data(self) -> None:
        """Test keeps the extractor's generic guess when no reader is given."""
        use_case = PreviewPdfImport(FakePdfPayrollExtractor())

        result = await use_case.execute("payslip.pdf", b"content")

        assert result.payment_date == date(2026, 1, 30)

    @pytest.mark.asyncio
    async def test_keeps_generic_guess_when_employer_rule_unknown(self) -> None:
        """Test keeps the generic guess when the employer isn't registered."""
        reader = FakeEmployerPaymentRuleReader(rule=None)
        use_case = PreviewPdfImport(FakePdfPayrollExtractor(), reader)

        result = await use_case.execute("payslip.pdf", b"content")

        assert reader.requested_names == ["ACME"]
        assert result.payment_date == date(2026, 1, 30)

    @pytest.mark.asyncio
    async def test_overrides_payment_date_with_real_employer_rule(self) -> None:
        """Test recomputes payment_date from the employer's real rule."""
        rule = EmployerPaymentRuleDTO(
            country_code="CL",
            payment_date_rule="last_business_day_of_month",
            payment_month_offset=0,
            payment_day_of_month=None,
            payment_business_day_offset=1,
            payment_calendar_day_offset=0,
            payment_effective_on_processing_next_day=True,
            payment_fixed_day_roll="previous_business_day",
        )
        reader = FakeEmployerPaymentRuleReader(rule=rule)
        extractor = FakePdfPayrollExtractor()
        use_case = PreviewPdfImport(extractor, reader)

        result = await use_case.execute("payslip.pdf", b"content")

        assert result.payment_date != date(2026, 1, 30)
        assert result.employer == "ACME"
        assert result.template_id == "acme-v1"

    @pytest.mark.asyncio
    async def test_skips_lookup_when_extractor_could_not_resolve_employer(
        self,
    ) -> None:
        """Test never looks up a rule when the PDF matched no template."""

        class UnresolvedExtractor:
            """Extractor double returning an employer-less preview."""

            def extract_preview(
                self, filename: str, content: bytes
            ) -> PdfImportPreviewDTO:
                """Extract preview."""
                return PdfImportPreviewDTO(
                    employer=None,
                    period_year=None,
                    period_month=None,
                    payment_date=None,
                    worked_days=None,
                    declared_net_pay_clp=None,
                    employment_contract_kind=None,
                    template_id=None,
                    rows=[],
                )

        reader = FakeEmployerPaymentRuleReader(rule=None)
        use_case = PreviewPdfImport(UnresolvedExtractor(), reader)

        result = await use_case.execute("payslip.pdf", b"content")

        assert reader.requested_names == []
        assert result.payment_date is None
