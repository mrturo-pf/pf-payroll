"""Template-based implementation of the PdfPayrollExtractor port."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from payroll.application.dto import (
    PayrollConceptKind,
    PdfImportPreviewDTO,
    PdfImportPreviewRowDTO,
)
from payroll.application.ports.pdf_extractors import PdfPayrollExtractor
from payroll.infrastructure.logging.logger import logger
from payroll.infrastructure.pdf_import.templates import (
    Template,
    load_templates,
    match_field,
    select_template,
)
from payroll.infrastructure.pdf_import.text_extraction import (
    extract_raw_text,
    find_amount_start_column,
    find_detail_lines,
    find_income_discount_column_boundary,
    parse_detail_line,
    parse_header,
)

_UNMATCHED_CONFIDENCE = 0.0
_FALLBACK_KIND: PayrollConceptKind = "income"


class TemplatePdfPayrollExtractor(PdfPayrollExtractor):
    """Extracts a payroll PDF preview using versioned JSON templates.

    Never raises: any failure (unparsable PDF, no template match, no detail
    rows found) degrades to an emptier PdfImportPreviewDTO rather than an
    exception, per the endpoint 1 contract.
    """

    def __init__(self, templates_dir: Path | None = None) -> None:
        """Initialize the instance, loading all templates once."""
        self._templates: list[Template] = load_templates(templates_dir)

    def extract_preview(self, filename: str, content: bytes) -> PdfImportPreviewDTO:
        """Extract a preview from PDF bytes."""
        try:
            return self._extract_preview(content)
        except Exception:  # noqa: BLE001 -- extraction must never raise
            logger.warning(
                "pdf_import.extraction_unexpected_failure", filename=filename
            )
            return _empty_preview()

    def _extract_preview(self, content: bytes) -> PdfImportPreviewDTO:
        raw_text = extract_raw_text(content)
        if raw_text is None:
            return _empty_preview()

        header = parse_header(raw_text)
        detail_lines = find_detail_lines(raw_text)
        parsed_by_line = [(line, parse_detail_line(line)) for line in detail_lines]
        resolved_lines = [
            (line, parsed) for line, parsed in parsed_by_line if parsed is not None
        ]
        labels = [parsed[0] for _, parsed in resolved_lines]

        template = select_template(self._templates, raw_text, labels)
        column_boundary = find_income_discount_column_boundary(raw_text)

        rows = [
            _build_row(label, amount, line, template, column_boundary)
            for line, (label, amount) in resolved_lines
        ]

        return PdfImportPreviewDTO(
            employer=template.employer_name if template else None,
            period_year=header.period_year,
            period_month=header.period_month,
            worked_days=header.worked_days,
            declared_net_pay_clp=header.declared_net_pay_clp,
            template_id=template.template_id if template else None,
            rows=rows,
        )


def _build_row(
    label: str,
    amount: Decimal,
    original_line: str,
    template: Template | None,
    column_boundary: int | None,
) -> PdfImportPreviewRowDTO:
    """Build a single preview row, resolving concept_code/kind/confidence."""
    field = match_field(template, label) if template else None
    if field is not None:
        return PdfImportPreviewRowDTO(
            raw_label=label,
            extracted_amount_clp=amount,
            kind=field.kind,
            concept_code=field.concept_code,
            confidence=field.confidence,
        )
    return PdfImportPreviewRowDTO(
        raw_label=label,
        extracted_amount_clp=amount,
        kind=_infer_unmatched_kind(original_line, column_boundary),
        concept_code=None,
        confidence=_UNMATCHED_CONFIDENCE,
    )


def _infer_unmatched_kind(line: str, column_boundary: int | None) -> PayrollConceptKind:
    """Best-effort kind guess for a row no template field matched.

    Uses the amount's column position relative to the document's own
    HABERES/DESCUENTOS header as a structural (not semantic) signal. Falls
    back to "income" when the boundary or the amount could not be located --
    this only affects display for a row that is already flagged unresolved
    (concept_code=None, confidence=0.0), it never reaches persistence as-is.
    """
    if column_boundary is None:
        return _FALLBACK_KIND
    amount_start = find_amount_start_column(line)
    if amount_start is None:  # pragma: no cover -- line already yielded an amount
        return _FALLBACK_KIND
    return "discount" if amount_start >= column_boundary else "income"


def _empty_preview() -> PdfImportPreviewDTO:
    """Return the safe, never-fails-loudly empty preview."""
    return PdfImportPreviewDTO(
        employer=None,
        period_year=None,
        period_month=None,
        worked_days=None,
        declared_net_pay_clp=None,
        template_id=None,
        rows=[],
    )
