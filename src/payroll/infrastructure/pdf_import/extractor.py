"""Template-based implementation of the PdfPayrollExtractor port."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from payroll.application.dto import (
    PayrollConceptKind,
    PdfImportPreviewDTO,
    PdfImportPreviewRowDTO,
)
from payroll.application.ports.pdf_extractors import PdfPayrollExtractor
from payroll.domain.contributions import EmploymentContractKind
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
from payroll.shared.constants import UNEMPLOYMENT_INSURANCE_CONCEPT_CODE
from payroll.shared.dates import resolve_payment_date

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
            payment_date=_resolve_payment_date(header.period_year, header.period_month),
            worked_days=header.worked_days,
            declared_net_pay_clp=header.declared_net_pay_clp,
            employment_contract_kind=_infer_employment_contract_kind(rows),
            template_id=template.template_id if template else None,
            rows=rows,
        )


def _infer_employment_contract_kind(
    rows: list[PdfImportPreviewRowDTO],
) -> EmploymentContractKind | None:
    """Best-effort indefinite/fixed_term inference from the payslip itself.

    Chilean law: unemployment insurance ("seguro de cesantia") only deducts
    from the employee on an indefinite contract (0.6% employee rate) --
    fixed-term contracts have a 0% employee rate (see
    contribution_calculator.py's compute_unemployment_contribution, the same
    domain rule this reuses rather than re-deriving). A resolved
    UNEMPLOYMENT_INSURANCE discount row with a positive amount is therefore
    self-contained evidence the payslip belongs to an indefinite contract; a
    zero amount (present but not actually deducted) means fixed_term.
    Stays unresolved (None) when no such row was found at all -- an
    unmatched template, or one that doesn't map this concept -- rather than
    guessing from nothing. Either way, this only pre-fills
    PdfImportPreviewResponse for a human to confirm or correct before
    POST /payroll/import/rows, which still requires employment_contract_kind
    explicitly -- this is never used to persist anything by itself.
    """
    matches = [
        row
        for row in rows
        if row.concept_code == UNEMPLOYMENT_INSURANCE_CONCEPT_CODE
        and row.kind == "discount"
    ]
    if not matches:
        return None
    return (
        EmploymentContractKind.INDEFINITE
        if any(row.amount_clp > 0 for row in matches)
        else EmploymentContractKind.FIXED_TERM
    )


def _resolve_payment_date(
    period_year: int | None, period_month: int | None
) -> date | None:
    """Best-effort payment_date: the generic last-Chilean-business-day rule.

    Same default `resolve_payment_date()` already falls back to elsewhere in
    the system (see docs/api.md's /payroll/period-range) when an employer's
    actual payment rule isn't known. This extractor has no database access
    itself (see `templates.py`'s module docstring -- template matching is
    pure static-file config), so it can never resolve a real per-employer
    override on its own; `PreviewPdfImport` is the one that may recompute
    this into the real employer rule afterwards, once the template match
    here has revealed which employer produced the PDF. A caller confirming
    this preview through POST /payroll/import/rows can still override
    payment_date by hand if either guess turns out wrong.
    """
    if period_year is None or period_month is None:
        return None
    return resolve_payment_date(period_year, period_month)


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
            amount_clp=amount,
            kind=field.kind,
            concept_code=field.concept_code,
            confidence=field.confidence,
        )
    return PdfImportPreviewRowDTO(
        raw_label=label,
        amount_clp=amount,
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
        payment_date=None,
        worked_days=None,
        declared_net_pay_clp=None,
        employment_contract_kind=None,
        template_id=None,
        rows=[],
    )
