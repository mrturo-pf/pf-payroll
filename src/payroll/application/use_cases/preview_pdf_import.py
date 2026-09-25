"""Use case for previewing payroll data extracted from a PDF."""

from payroll.application.errors import PayrollValidationError
from payroll.application.dto import PdfImportPreviewDTO
from payroll.application.ports.pdf_extractors import PdfPayrollExtractor


class PreviewPdfImport:
    """Extracts a payroll PDF preview without persisting anything.

    Deliberately has no PayrollRepository dependency -- this is the
    architectural guarantee that the preview endpoint can never write to the
    database nor trigger ProcessImportedPayrollPeriods.
    """

    def __init__(self, extractor: PdfPayrollExtractor) -> None:
        """Initialize the instance."""
        self._extractor = extractor

    async def execute(self, filename: str, content: bytes) -> PdfImportPreviewDTO:
        """Extract a preview from the given PDF bytes."""
        if not filename:
            raise PayrollValidationError("A PDF file name is required.")
        return self._extractor.extract_preview(filename, content)
