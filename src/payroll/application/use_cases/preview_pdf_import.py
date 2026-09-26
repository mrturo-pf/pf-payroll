"""Use case for previewing payroll data extracted from a PDF."""

from dataclasses import replace

from payroll.application.errors import PayrollValidationError
from payroll.application.dto import PdfImportPreviewDTO
from payroll.application.ports.pdf_extractors import PdfPayrollExtractor
from payroll.application.ports.repositories import EmployerPaymentRuleReader
from payroll.shared.dates import resolve_payment_date


class PreviewPdfImport:
    """Extracts a payroll PDF preview without persisting anything.

    Deliberately has no `PayrollRepository` dependency -- this is the
    architectural guarantee that the preview endpoint can never write to the
    database nor trigger `ProcessImportedPayrollPeriods`. It *may* optionally
    take a read-only `EmployerPaymentRuleReader` to resolve the real
    employer payment-date rule once the extractor's template match reveals
    which employer produced the PDF -- "no persistence" only ever meant "no
    writes", never "no reads": a read cannot corrupt state, so there is no
    architectural reason to forbid it here.
    """

    def __init__(
        self,
        extractor: PdfPayrollExtractor,
        reference_data: EmployerPaymentRuleReader | None = None,
    ) -> None:
        """Initialize the instance."""
        self._extractor = extractor
        self._reference_data = reference_data

    async def execute(self, filename: str, content: bytes) -> PdfImportPreviewDTO:
        """Extract a preview from the given PDF bytes."""
        if not filename:
            raise PayrollValidationError("A PDF file name is required.")
        preview = self._extractor.extract_preview(filename, content)
        return await self._with_real_payment_date(preview)

    async def _with_real_payment_date(
        self, preview: PdfImportPreviewDTO
    ) -> PdfImportPreviewDTO:
        """Recompute payment_date using the employer's real rule, if known.

        Falls back to the extractor's generic guess (last business day of
        the month, no employer-specific offsets) whenever no repository was
        given, the employer/period could not be resolved from the PDF
        itself, or no employer with that exact name is registered yet --
        every one of those is a legitimate "cannot know better" case, not an
        error.
        """
        if (
            self._reference_data is None
            or preview.employer is None
            or preview.period_year is None
            or preview.period_month is None
        ):
            return preview
        rule = await self._reference_data.get_employer_payment_rule(preview.employer)
        if rule is None:
            return preview
        payment_date = resolve_payment_date(
            preview.period_year,
            preview.period_month,
            country_code=rule.country_code,
            payment_date_rule=rule.payment_date_rule,
            payment_month_offset=rule.payment_month_offset,
            payment_day_of_month=rule.payment_day_of_month,
            payment_business_day_offset=rule.payment_business_day_offset,
            payment_calendar_day_offset=rule.payment_calendar_day_offset,
            payment_effective_on_processing_next_day=(
                rule.payment_effective_on_processing_next_day
            ),
            payment_fixed_day_roll=rule.payment_fixed_day_roll,
        )
        return replace(preview, payment_date=payment_date)
