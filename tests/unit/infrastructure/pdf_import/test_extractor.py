"""Tests for TemplatePdfPayrollExtractor."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from payroll.application.dto import PdfTemplateDTO, PdfTemplateFieldDTO
from payroll.domain.contributions import EmploymentContractKind
from payroll.infrastructure.pdf_import.extractor import TemplatePdfPayrollExtractor

SYNTHETIC_TEXT = (
    "ACME Corp S.A\n"
    "R.U.T. NOMBRE MES AÑO\n"
    "11.111.111-1 DOE JANE Marzo 2026\n"
    "\n"
    "LUGAR DE TRABAJO ANTIGUEDAD LABORAL DIAS TRABAJADOS\n"
    "S1 01.01.2020 25\n"
    "\n"
    "DETALLE HABERES Y DESCUENTOS   CODIGO  CUOTAS   HABERES   DESCUENTOS\n"
    "SUELDO 1000                                   100,000\n"
    "GRATIFICACION LEGAL 1050                       20,000\n"
    "BONO SORPRESA 9Z12                                5,000\n"
    "IMPUESTO /370                                                      50,000\n"
    "\n"
    "TOTALES 1,220,000 50,000\n"
    "LIQUIDO  A PAGAR 1,170,000\n"
)


_SHARED_FILLER_FIELDS = (
    PdfTemplateFieldDTO(
        id=1,
        pdf_label_pattern="(?i)^SUELDO$",
        concept_code="SALARY_BASE",
        kind="income",
        confidence=0.9,
    ),
    PdfTemplateFieldDTO(
        id=2,
        pdf_label_pattern="(?i)GRATIFICACION",
        concept_code="LEGAL_GRATUITY",
        kind="income",
        confidence=0.9,
    ),
)


def _single_acme_template(extra_field: PdfTemplateFieldDTO) -> list[PdfTemplateDTO]:
    """Build one ACME template: 2 shared filler fields plus one under test.

    select_template() requires MIN_TEMPLATE_MATCH_SCORE (3) distinct fields
    to actually match a detail label before picking a template at all -- a
    template with only the one field under test would never be selected.
    """
    return [
        PdfTemplateDTO(
            id=1,
            template_id="acme-v1",
            employer_id=None,
            employer_name="ACME",
            employer_match_pattern="(?i)acme",
            version=1,
            is_active=True,
            fields=[*_SHARED_FILLER_FIELDS, extra_field],
        )
    ]


def _acme_templates() -> list[PdfTemplateDTO]:
    """Build the shared ACME test template fixture, as TemplateReader returns it."""
    return _single_acme_template(
        PdfTemplateFieldDTO(
            id=3,
            pdf_label_pattern="(?i)^IMPUESTO$",
            concept_code="INCOME_TAX",
            kind="discount",
            confidence=0.9,
        )
    )


def _unemployment_templates() -> list[PdfTemplateDTO]:
    """Build a template mapping UNEMPLOYMENT_INSURANCE plus 2 filler fields."""
    return _single_acme_template(
        PdfTemplateFieldDTO(
            id=3,
            pdf_label_pattern="(?i)CESANTIA",
            concept_code="UNEMPLOYMENT_INSURANCE",
            kind="discount",
            confidence=0.9,
        )
    )


def _unemployment_insurance_text(amount: str) -> str:
    """Build a synthetic payslip with one UNEMPLOYMENT_INSURANCE discount row.

    Includes 2 filler income rows purely so the template clears
    MIN_TEMPLATE_MATCH_SCORE and actually gets selected (see
    _unemployment_templates).
    """
    return (
        "ACME Corp S.A\n"
        "R.U.T. NOMBRE MES AÑO\n"
        "11.111.111-1 DOE JANE Marzo 2026\n"
        "\n"
        "DETALLE HABERES Y DESCUENTOS   CODIGO  CUOTAS   HABERES   DESCUENTOS\n"
        "SUELDO 1000                                   100,000\n"
        "GRATIFICACION LEGAL 1050                       20,000\n"
        f"SEGURO CESANTIA 1E89                                       {amount}\n"
        "\n"
        f"TOTALES 120,000 {amount}\n"
        "LIQUIDO  A PAGAR 900,000\n"
    )


def _extract_with_mocked_text(
    extractor: TemplatePdfPayrollExtractor,
    text: str | None,
    templates: list[PdfTemplateDTO],
):
    """Run extract_preview with extract_raw_text mocked to return `text`."""
    with patch(
        "payroll.infrastructure.pdf_import.extractor.extract_raw_text",
        return_value=text,
    ):
        return extractor.extract_preview("sample.pdf", b"irrelevant", templates)


class TestTemplatePdfPayrollExtractor:
    """Tests for TemplatePdfPayrollExtractor."""

    def test_returns_empty_preview_when_text_extraction_fails(self) -> None:
        """Test returns empty preview when text extraction fails."""
        extractor = TemplatePdfPayrollExtractor()
        preview = _extract_with_mocked_text(extractor, None, _acme_templates())
        assert preview.employer is None
        assert preview.template_id is None
        assert preview.payment_date is None
        assert preview.rows == []

    def test_matches_template_and_resolves_known_concepts(self) -> None:
        """Test matches template and resolves known concepts."""
        extractor = TemplatePdfPayrollExtractor()
        preview = _extract_with_mocked_text(
            extractor, SYNTHETIC_TEXT, _acme_templates()
        )

        assert preview.employer == "ACME"
        assert preview.template_id == "acme-v1"
        assert preview.period_year == 2026
        assert preview.period_month == 3
        assert preview.payment_date == date(2026, 3, 31)
        assert preview.worked_days == 25
        assert preview.declared_net_pay_clp == Decimal("1170000")
        assert len(preview.rows) == 4

        by_label = {row.raw_label: row for row in preview.rows}
        assert by_label["SUELDO"].concept_code == "SALARY_BASE"
        assert by_label["SUELDO"].kind == "income"
        assert by_label["SUELDO"].confidence == 0.9
        assert by_label["IMPUESTO"].concept_code == "INCOME_TAX"
        assert by_label["IMPUESTO"].kind == "discount"

    def test_unmatched_line_falls_back_to_column_position_for_kind(self) -> None:
        """Unmatched BONO SORPRESA infers income from its column position."""
        extractor = TemplatePdfPayrollExtractor()
        preview = _extract_with_mocked_text(
            extractor, SYNTHETIC_TEXT, _acme_templates()
        )

        unmatched = next(r for r in preview.rows if r.raw_label == "BONO SORPRESA")
        assert unmatched.concept_code is None
        assert unmatched.confidence == 0.0
        assert unmatched.kind == "income"

    def test_unmatched_line_uses_income_fallback_when_no_column_header_found(
        self,
    ) -> None:
        """No HABERES/DESCUENTOS header row means the fallback stays income."""
        no_boundary_text = SYNTHETIC_TEXT.replace(
            "DETALLE HABERES Y DESCUENTOS   CODIGO  CUOTAS   HABERES   DESCUENTOS\n",
            "DETALLE DE CONCEPTOS\n",
        )
        extractor = TemplatePdfPayrollExtractor()
        preview = _extract_with_mocked_text(
            extractor, no_boundary_text, _acme_templates()
        )

        assert preview.template_id == "acme-v1"
        unmatched = next(r for r in preview.rows if r.raw_label == "BONO SORPRESA")
        assert unmatched.concept_code is None
        assert unmatched.kind == "income"

    def test_no_employer_match_returns_unresolved_rows_with_header_still_parsed(
        self,
    ) -> None:
        """Test no employer match returns unresolved rows with header still parsed."""
        other_company_text = SYNTHETIC_TEXT.replace("ACME Corp S.A", "OTHER CORP")
        extractor = TemplatePdfPayrollExtractor()
        preview = _extract_with_mocked_text(
            extractor, other_company_text, _acme_templates()
        )

        assert preview.employer is None
        assert preview.template_id is None
        assert preview.period_year == 2026
        assert preview.period_month == 3
        assert len(preview.rows) == 4
        assert all(row.concept_code is None for row in preview.rows)
        assert all(row.confidence == 0.0 for row in preview.rows)
        by_label = {row.raw_label: row for row in preview.rows}
        assert by_label["SUELDO"].kind == "income"
        assert by_label["IMPUESTO"].kind == "discount"

    def test_payment_date_is_none_when_period_could_not_be_parsed(self) -> None:
        """payment_date stays None when the period header itself is unknown.

        Unlike the fully-empty-preview case (no text at all), this covers a
        PDF that extracted fine but simply has no Spanish month+year pair to
        match -- resolve_payment_date() needs both period_year and
        period_month, so it must never be called with either missing.
        """
        no_period_text = SYNTHETIC_TEXT.replace("Marzo 2026", "UNKNOWN PERIOD")
        extractor = TemplatePdfPayrollExtractor()
        preview = _extract_with_mocked_text(
            extractor, no_period_text, _acme_templates()
        )

        assert preview.period_year is None
        assert preview.period_month is None
        assert preview.payment_date is None

    def test_positive_unemployment_insurance_discount_infers_indefinite(self) -> None:
        """A nonzero employee-side UNEMPLOYMENT_INSURANCE discount => indefinite.

        Chilean law: this discount only applies to the employee at all on an
        indefinite contract (see contribution_calculator.py) -- a positive
        amount is therefore self-contained evidence.
        """
        extractor = TemplatePdfPayrollExtractor()
        preview = _extract_with_mocked_text(
            extractor, _unemployment_insurance_text("5,000"), _unemployment_templates()
        )
        assert preview.employment_contract_kind == EmploymentContractKind.INDEFINITE

    def test_zero_unemployment_insurance_discount_infers_fixed_term(self) -> None:
        """A present-but-zero UNEMPLOYMENT_INSURANCE discount => fixed_term."""
        extractor = TemplatePdfPayrollExtractor()
        preview = _extract_with_mocked_text(
            extractor, _unemployment_insurance_text("0,000"), _unemployment_templates()
        )
        assert preview.employment_contract_kind == EmploymentContractKind.FIXED_TERM

    def test_no_unemployment_insurance_row_stays_unresolved(self) -> None:
        """No matching row at all leaves employment_contract_kind unresolved."""
        extractor = TemplatePdfPayrollExtractor()
        preview = _extract_with_mocked_text(
            extractor, SYNTHETIC_TEXT, _acme_templates()
        )
        assert preview.employment_contract_kind is None

    def test_no_templates_loaded_returns_unresolved_rows(self) -> None:
        """Test no templates loaded returns unresolved rows."""
        extractor = TemplatePdfPayrollExtractor()
        preview = _extract_with_mocked_text(extractor, SYNTHETIC_TEXT, [])
        assert preview.template_id is None
        assert len(preview.rows) == 4

    def test_never_raises_on_unexpected_internal_failure(self) -> None:
        """Test never raises on unexpected internal failure."""
        extractor = TemplatePdfPayrollExtractor()
        with (
            patch(
                "payroll.infrastructure.pdf_import.extractor.extract_raw_text",
                return_value=SYNTHETIC_TEXT,
            ),
            patch(
                "payroll.infrastructure.pdf_import.extractor.parse_header",
                side_effect=RuntimeError("boom"),
            ),
        ):
            preview = extractor.extract_preview(
                "sample.pdf", b"whatever", _acme_templates()
            )
        assert preview.rows == []
        assert preview.employer is None

    def test_extract_preview_reads_real_pdf_bytes_end_to_end(self) -> None:
        """Sanity check the PdfReader wiring itself (no mocking of text_extraction)."""
        extractor = TemplatePdfPayrollExtractor()
        preview = extractor.extract_preview(
            "sample.pdf", b"%PDF-1.4 not a real pdf", _acme_templates()
        )
        assert preview.rows == []
        assert preview.employer is None
