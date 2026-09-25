"""Tests for TemplatePdfPayrollExtractor."""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

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

ACME_TEMPLATE = {
    "template_id": "acme-v1",
    "employer_name": "ACME",
    "version": 1,
    "employer_match": {"name_pattern": "(?i)acme"},
    "fields": [
        {
            "pdf_label_pattern": "(?i)^SUELDO$",
            "concept_code": "SALARY_BASE",
            "kind": "income",
            "confidence": 0.9,
        },
        {
            "pdf_label_pattern": "(?i)GRATIFICACION",
            "concept_code": "LEGAL_GRATUITY",
            "kind": "income",
            "confidence": 0.9,
        },
        {
            "pdf_label_pattern": "(?i)^IMPUESTO$",
            "concept_code": "INCOME_TAX",
            "kind": "discount",
            "confidence": 0.9,
        },
    ],
}


def _write_acme_template(tmp_path: Path) -> Path:
    """Write the shared ACME test template fixture to disk."""
    templates_dir = tmp_path / "acme"
    templates_dir.mkdir(parents=True, exist_ok=True)
    (templates_dir / "v1.json").write_text(json.dumps(ACME_TEMPLATE), encoding="utf-8")
    return tmp_path


def _extract_with_mocked_text(extractor: TemplatePdfPayrollExtractor, text: str | None):
    """Run extract_preview with extract_raw_text mocked to return `text`."""
    with patch(
        "payroll.infrastructure.pdf_import.extractor.extract_raw_text",
        return_value=text,
    ):
        return extractor.extract_preview("sample.pdf", b"irrelevant")


class TestTemplatePdfPayrollExtractor:
    """Tests for TemplatePdfPayrollExtractor."""

    def test_returns_empty_preview_when_text_extraction_fails(
        self, tmp_path: Path
    ) -> None:
        """Test returns empty preview when text extraction fails."""
        extractor = TemplatePdfPayrollExtractor(_write_acme_template(tmp_path))
        preview = _extract_with_mocked_text(extractor, None)
        assert preview.employer is None
        assert preview.template_id is None
        assert preview.rows == []

    def test_matches_template_and_resolves_known_concepts(self, tmp_path: Path) -> None:
        """Test matches template and resolves known concepts."""
        extractor = TemplatePdfPayrollExtractor(_write_acme_template(tmp_path))
        preview = _extract_with_mocked_text(extractor, SYNTHETIC_TEXT)

        assert preview.employer == "ACME"
        assert preview.template_id == "acme-v1"
        assert preview.period_year == 2026
        assert preview.period_month == 3
        assert preview.worked_days == 25
        assert preview.declared_net_pay_clp == Decimal("1170000")
        assert len(preview.rows) == 4

        by_label = {row.raw_label: row for row in preview.rows}
        assert by_label["SUELDO"].concept_code == "SALARY_BASE"
        assert by_label["SUELDO"].kind == "income"
        assert by_label["SUELDO"].confidence == 0.9
        assert by_label["IMPUESTO"].concept_code == "INCOME_TAX"
        assert by_label["IMPUESTO"].kind == "discount"

    def test_unmatched_line_falls_back_to_column_position_for_kind(
        self, tmp_path: Path
    ) -> None:
        """Unmatched BONO SORPRESA infers income from its column position."""
        extractor = TemplatePdfPayrollExtractor(_write_acme_template(tmp_path))
        preview = _extract_with_mocked_text(extractor, SYNTHETIC_TEXT)

        unmatched = next(r for r in preview.rows if r.raw_label == "BONO SORPRESA")
        assert unmatched.concept_code is None
        assert unmatched.confidence == 0.0
        assert unmatched.kind == "income"

    def test_unmatched_line_uses_income_fallback_when_no_column_header_found(
        self, tmp_path: Path
    ) -> None:
        """No HABERES/DESCUENTOS header row means the fallback stays income."""
        no_boundary_text = SYNTHETIC_TEXT.replace(
            "DETALLE HABERES Y DESCUENTOS   CODIGO  CUOTAS   HABERES   DESCUENTOS\n",
            "DETALLE DE CONCEPTOS\n",
        )
        extractor = TemplatePdfPayrollExtractor(_write_acme_template(tmp_path))
        preview = _extract_with_mocked_text(extractor, no_boundary_text)

        assert preview.template_id == "acme-v1"
        unmatched = next(r for r in preview.rows if r.raw_label == "BONO SORPRESA")
        assert unmatched.concept_code is None
        assert unmatched.kind == "income"

    def test_no_employer_match_returns_unresolved_rows_with_header_still_parsed(
        self, tmp_path: Path
    ) -> None:
        """Test no employer match returns unresolved rows with header still parsed."""
        other_company_text = SYNTHETIC_TEXT.replace("ACME Corp S.A", "OTHER CORP")
        extractor = TemplatePdfPayrollExtractor(_write_acme_template(tmp_path))
        preview = _extract_with_mocked_text(extractor, other_company_text)

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

    def test_no_templates_loaded_returns_unresolved_rows(self, tmp_path: Path) -> None:
        """Test no templates loaded returns unresolved rows."""
        extractor = TemplatePdfPayrollExtractor(tmp_path / "empty")
        preview = _extract_with_mocked_text(extractor, SYNTHETIC_TEXT)
        assert preview.template_id is None
        assert len(preview.rows) == 4

    def test_never_raises_on_unexpected_internal_failure(self, tmp_path: Path) -> None:
        """Test never raises on unexpected internal failure."""
        extractor = TemplatePdfPayrollExtractor(_write_acme_template(tmp_path))
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
            preview = extractor.extract_preview("sample.pdf", b"whatever")
        assert preview.rows == []
        assert preview.employer is None

    def test_extract_preview_reads_real_pdf_bytes_end_to_end(
        self, tmp_path: Path
    ) -> None:
        """Sanity check the PdfReader wiring itself (no mocking of text_extraction)."""
        extractor = TemplatePdfPayrollExtractor(_write_acme_template(tmp_path))
        preview = extractor.extract_preview("sample.pdf", b"%PDF-1.4 not a real pdf")
        assert preview.rows == []
        assert preview.employer is None
