"""Port definitions for payroll PDF preview extraction adapters."""

from typing import Protocol

from payroll.application.dto import PdfImportPreviewDTO, PdfTemplateDTO


class PdfPayrollExtractor(Protocol):
    """Reads a payroll PDF into a preview DTO, without any persistence.

    Implementations must never raise on malformed/unrecognized PDFs -- the
    worst-case result is a PdfImportPreviewDTO with unresolved fields, never
    an exception. This mirrors PayrollImporter but is intentionally a
    separate port: the preview output (per-row confidence, unmatched
    concepts) is a materially richer shape than the flat ImportPayrollRowDTO,
    and this extractor is never wired to a PayrollRepository.
    """

    def extract_preview(
        self, filename: str, content: bytes, templates: list[PdfTemplateDTO]
    ) -> PdfImportPreviewDTO:
        """Extract a preview from PDF bytes, matching against the given templates.

        `templates` is supplied by the caller (PreviewPdfImport, via
        TemplateReader) rather than loaded by the implementation itself --
        this port has no storage/database access of its own.
        """
        ...
