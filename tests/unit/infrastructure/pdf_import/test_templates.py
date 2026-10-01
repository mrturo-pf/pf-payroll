"""Tests for payroll PDF template compilation and matching."""

import pytest

from payroll.application.dto import PdfTemplateDTO, PdfTemplateFieldDTO
from payroll.infrastructure.pdf_import.templates import (
    MIN_TEMPLATE_MATCH_SCORE,
    Template,
    compile_templates,
    match_field,
    select_template,
)


def _acme_dto(
    version: int = 1, fields: list[PdfTemplateFieldDTO] | None = None
) -> PdfTemplateDTO:
    """Build a minimal, valid template DTO for tests."""
    return PdfTemplateDTO(
        id=version,
        template_id=f"acme-v{version}",
        employer_id=None,
        employer_name="ACME",
        employer_match_pattern="(?i)acme",
        version=version,
        is_active=True,
        fields=fields
        if fields is not None
        else [
            PdfTemplateFieldDTO(
                id=1,
                pdf_label_pattern="(?i)^SUELDO$",
                concept_code="SALARY_BASE",
                kind="income",
                confidence=0.9,
            ),
            PdfTemplateFieldDTO(
                id=2,
                pdf_label_pattern="(?i)IMPUESTO",
                concept_code="INCOME_TAX",
                kind="discount",
                confidence=0.9,
            ),
            PdfTemplateFieldDTO(
                id=3,
                pdf_label_pattern="(?i)SALUD",
                concept_code="HEALTH_BASE",
                kind="discount",
                confidence=0.6,
            ),
        ],
    )


class TestCompileTemplates:
    """Tests for compile_templates."""

    def test_compiles_every_dto_given(self) -> None:
        """Test compiles every dto given."""
        templates = compile_templates([_acme_dto()])
        assert len(templates) == 1
        template = templates[0]
        assert isinstance(template, Template)
        assert template.template_id == "acme-v1"
        assert template.employer_name == "ACME"
        assert template.version == 1
        assert len(template.fields) == 3

    def test_defaults_confidence_when_dto_omits_it(self) -> None:
        """PdfTemplateFieldDTO.confidence defaults to 0.9, as the old JSON did."""
        dto = _acme_dto(
            fields=[
                PdfTemplateFieldDTO(
                    id=1,
                    pdf_label_pattern="(?i)^SUELDO$",
                    concept_code="SALARY_BASE",
                    kind="income",
                )
            ]
        )
        template = compile_templates([dto])[0]
        assert template.fields[0].confidence == 0.9

    def test_returns_empty_list_for_empty_input(self) -> None:
        """Test returns empty list for empty input."""
        assert compile_templates([]) == []


class TestSelectTemplate:
    """Tests for select_template."""

    @pytest.fixture
    def templates(self) -> list[Template]:
        """Compile a v1/v2 ACME template pair for scoring tests."""
        v2 = _acme_dto(
            version=2,
            fields=[
                PdfTemplateFieldDTO(
                    id=1,
                    pdf_label_pattern="(?i)^SUELDO$",
                    concept_code="SALARY_BASE",
                    kind="income",
                ),
                PdfTemplateFieldDTO(
                    id=2,
                    pdf_label_pattern="(?i)IMPUESTO",
                    concept_code="INCOME_TAX",
                    kind="discount",
                ),
                PdfTemplateFieldDTO(
                    id=3,
                    pdf_label_pattern="(?i)SALUD",
                    concept_code="HEALTH_BASE",
                    kind="discount",
                ),
                PdfTemplateFieldDTO(
                    id=4,
                    pdf_label_pattern="(?i)GRATIFICACION",
                    concept_code="LEGAL_GRATUITY",
                    kind="income",
                ),
            ],
        )
        return compile_templates([_acme_dto(version=1), v2])

    def test_returns_none_when_no_employer_matches(
        self, templates: list[Template]
    ) -> None:
        """Test returns none when no employer matches."""
        assert select_template(templates, "some other company", ["SUELDO"]) is None

    def test_returns_none_when_score_below_threshold(
        self, templates: list[Template]
    ) -> None:
        """Test returns none when score below threshold."""
        assert MIN_TEMPLATE_MATCH_SCORE == 3
        result = select_template(templates, "ACME S.A.", ["SUELDO"])
        assert result is None

    def test_picks_highest_scoring_candidate(self, templates: list[Template]) -> None:
        """v2 matches one extra label (GRATIFICACION), so it should win."""
        labels = ["SUELDO", "IMPUESTO", "SALUD BASE", "GRATIFICACION LEGAL"]
        result = select_template(templates, "ACME S.A.", labels)
        assert result is not None
        assert result.version == 2

    def test_picks_only_candidate_when_scores_tie(self) -> None:
        """Test picks only candidate when scores tie."""
        templates = compile_templates([_acme_dto(version=1)])
        labels = ["SUELDO", "IMPUESTO", "SALUD BASE"]
        result = select_template(templates, "ACME S.A.", labels)
        assert result is not None
        assert result.template_id == "acme-v1"


class TestMatchField:
    """Tests for match_field."""

    def test_returns_first_matching_field(self) -> None:
        """Test returns first matching field."""
        template = compile_templates([_acme_dto()])[0]
        field = match_field(template, "SUELDO")
        assert field is not None
        assert field.concept_code == "SALARY_BASE"

    def test_returns_none_when_nothing_matches(self) -> None:
        """Test returns none when nothing matches."""
        template = compile_templates([_acme_dto()])[0]
        assert match_field(template, "UNKNOWN LABEL") is None


def _corporative_chile_v1_dto() -> PdfTemplateDTO:
    """Build the real, shipped walmart-chile-v1 template as a hardcoded fixture.

    This is a Python-literal mirror of the former
    infrastructure/pdf_import/templates/walmart-chile/v1.json (deleted --
    templates now live in pf-db's PAY_PDF_TEMPLATE*, see
    docs/proposals/pdf-template-management-design-plan.md), and of the
    identical data seeded by pf-db's db/04_seed_real.sql. Kept here, not
    read from a live database, so this remains a DB-free *unit* test of the
    matching logic -- the real SQL query against the seeded row gets its own
    separate *integration* test.
    """
    return PdfTemplateDTO(
        id=1,
        template_id="walmart-chile-v1",
        employer_id=None,
        employer_name="WALMART-CHILE",
        employer_match_pattern=r"(?i)walmart-chile|walmart\s+chile",
        version=1,
        is_active=True,
        fields=[
            PdfTemplateFieldDTO(1, r"(?i)^SUELDO$", "SALARY_BASE", "income", 0.9),
            PdfTemplateFieldDTO(
                2, r"(?i)GRATIFICACION\s+LEGAL", "LEGAL_GRATUITY", "income", 0.9
            ),
            PdfTemplateFieldDTO(
                3,
                r"(?i)ASIGNACI[OÓ]N\s+TRAB\.?\s+H[IÍ]BRIDO",
                "TELEWORK_REFUND",
                "income",
                0.6,
            ),
            PdfTemplateFieldDTO(
                4,
                r"(?i)APORTE\s+SEGURO\s+DE\s+SALUD",
                "HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION",
                "income",
                0.75,
            ),
            PdfTemplateFieldDTO(5, r"(?i)^IMPUESTO$", "INCOME_TAX", "discount", 0.9),
            PdfTemplateFieldDTO(
                6,
                r"(?i)COT\.\s*SEG\.\s*CES\.",
                "UNEMPLOYMENT_INSURANCE",
                "discount",
                0.9,
            ),
            PdfTemplateFieldDTO(
                7, r"(?i)ESENCIAL\s+LEGAL", "HEALTH_BASE", "discount", 0.6
            ),
            PdfTemplateFieldDTO(
                8, r"(?i)COMISI[OÓ]N\s+AFP", "PENSION_ADDITIONAL", "discount", 0.9
            ),
            PdfTemplateFieldDTO(
                9, r"(?i)FONDO\s+RETIRO\s+AFP", "PENSION_BASE", "discount", 0.6
            ),
            PdfTemplateFieldDTO(
                10, r"(?i)ESENCIAL\s+ADICIONAL", "HEALTH_ADDITIONAL_UF", "discount", 0.6
            ),
            PdfTemplateFieldDTO(
                11,
                r"(?i)SEGURO\s+(DENTAL|DE\s+SALUD|CATASTR[OÓ]FICO)",
                "HEALTH_INSURANCE",
                "discount",
                0.9,
            ),
            PdfTemplateFieldDTO(12, r"(?i)^AGUINALDO", "HOLIDAY_BONUS", "income", 0.85),
            PdfTemplateFieldDTO(
                13,
                r"(?i)ANTICIPO\s+AGUINALDO",
                "HOLIDAY_BONUS_ADVANCE",
                "discount",
                0.9,
            ),
            PdfTemplateFieldDTO(
                14,
                r"(?i)BONO\s+POR\s+DISPONIBILIDAD",
                "AVAILABILITY_BONUS",
                "income",
                0.9,
            ),
            PdfTemplateFieldDTO(
                15,
                r"(?i)REAJUSTE\s+GRATI\.?\s*MENSUAL",
                "LEGAL_GRATUITY_ADJUSTMENT",
                "income",
                0.85,
            ),
            PdfTemplateFieldDTO(
                16, r"(?i)INCENTIVO\s+VACACIONES", "VACATION_INCENTIVE", "income", 0.9
            ),
            PdfTemplateFieldDTO(
                17,
                r"(?i)ANTICIPO\s+BONO\s+VACACIONES",
                "VACATION_BONUS_ADVANCE",
                "discount",
                0.9,
            ),
            PdfTemplateFieldDTO(
                18,
                r"(?i)DSCTO\s+LICEN[\s-]*AUSEN\s+MES\s+ANT",
                "PRIOR_MONTH_LEAVE_ABSENCE_DISCOUNT",
                "discount",
                0.85,
            ),
            PdfTemplateFieldDTO(
                19,
                r"(?i)DIF\.?\s*SUELDO\s+MES\s+ANTERIOR",
                "PRIOR_SALARY_DIFFERENCE",
                "income",
                0.85,
            ),
            PdfTemplateFieldDTO(
                20, r"(?i)^CCAF\s+.*VIGENTE$", "CCAF_LOAN", "discount", 0.9
            ),
        ],
    )


def _load_real_shipped_template(template_id: str = "walmart-chile-v1") -> Template:
    """Compile the real shipped template fixture, shared by every test below."""
    return next(
        t
        for t in compile_templates([_corporative_chile_v1_dto()])
        if t.template_id == template_id
    )


class TestAfpCommissionRegression:
    """Regression tests for the real shipped walmart-chile-v1 mapping.

    AFP commission ("COMISIÓN AFP") is a pension-contribution line item in
    Chile (and, per INCOME_TAX_DEDUCTIBLE_CONCEPT_CODES, tax-deductible),
    never a health one -- it was previously mis-mapped to
    HEALTH_ADDITIONAL_UF instead of PENSION_ADDITIONAL, which silently
    starved every payroll_period's PENSION_ADDITIONAL concept, permanently
    blocking net_pay reconciliation (see REVIEW_REQUIRED_CONCEPT_CODES in
    payroll.shared.constants -- it requires all 5 required concepts,
    PENSION_ADDITIONAL included, to be present before expected_net_pay_clp is
    ever computed).
    """

    def test_afp_commission_maps_to_pension_additional(self) -> None:
        """The AFP commission label must resolve to PENSION_ADDITIONAL."""
        template = _load_real_shipped_template()
        field = match_field(template, "COMISIÓN AFP P. VITAL")
        assert field is not None
        assert field.concept_code == "PENSION_ADDITIONAL"

    def test_isapre_additional_plan_still_maps_to_health_additional_uf(self) -> None:
        """The real Isapre extra-plan line (ESENCIAL ADICIONAL) is untouched."""
        template = _load_real_shipped_template()
        field = match_field(template, "ESENCIAL ADICIONAL")
        assert field is not None
        assert field.concept_code == "HEALTH_ADDITIONAL_UF"


class TestPayslipVariantConceptCoverage:
    """Regression tests covering payslip variants beyond a plain monthly payslip.

    A 2026-09 audit of every liquidación in secrets/liquidacion/ (22 real
    Walmart-Chile payslips spanning 2024-11 through 2026-08) found 8 raw
    labels the template didn't recognize yet -- all from months with a
    holiday bonus, a vacation bonus, an availability bonus, or a prior-month
    salary/leave adjustment, none of which appear in a bare monthly payslip.
    Every one of them already had a matching concept_code in pf-db's
    PAY_CONCEPT seed data (used by the CSV/XLSX importer already), so this
    was a template gap, not a missing domain concept.
    """

    @pytest.mark.parametrize(
        ("raw_label", "concept_code", "kind"),
        [
            ("AGUINALDO", "HOLIDAY_BONUS", "income"),
            ("AGUINALDO FIESTAS PATRIAS", "HOLIDAY_BONUS", "income"),
            ("ANTICIPO AGUINALDO", "HOLIDAY_BONUS_ADVANCE", "discount"),
            ("BONO POR DISPONIBILIDAD", "AVAILABILITY_BONUS", "income"),
            ("REAJUSTE GRATI. MENSUAL", "LEGAL_GRATUITY_ADJUSTMENT", "income"),
            ("INCENTIVO VACACIONES", "VACATION_INCENTIVE", "income"),
            ("ANTICIPO BONO VACACIONES", "VACATION_BONUS_ADVANCE", "discount"),
            (
                "DSCTO LICEN-AUSEN MES ANT",
                "PRIOR_MONTH_LEAVE_ABSENCE_DISCOUNT",
                "discount",
            ),
            ("DIF.SUELDO MES ANTERIOR", "PRIOR_SALARY_DIFFERENCE", "income"),
        ],
    )
    def test_variant_label_resolves_to_expected_concept(
        self, raw_label: str, concept_code: str, kind: str
    ) -> None:
        """Each payslip-variant raw label must resolve to its expected concept."""
        template = _load_real_shipped_template()
        field = match_field(template, raw_label)
        assert field is not None
        assert field.concept_code == concept_code
        assert field.kind == kind

    def test_holiday_bonus_advance_is_never_confused_with_holiday_bonus(self) -> None:
        """'ANTICIPO AGUINALDO' must never fall through to the '^AGUINALDO' field.

        Both HOLIDAY_BONUS and HOLIDAY_BONUS_ADVANCE share the substring
        'AGUINALDO', on opposite sides of the payslip (income vs discount) --
        this pins the anchored '^AGUINALDO' pattern so a future edit can't
        accidentally widen it into matching the advance line too.
        """
        template = _load_real_shipped_template()
        field = match_field(template, "ANTICIPO AGUINALDO")
        assert field is not None
        assert field.concept_code == "HOLIDAY_BONUS_ADVANCE"


class TestCcafLoanConceptCoverage:
    """Regression test for the CCAF social-credit deduction line.

    2026-09: 'CCAF LA ARAUCANA VIGENTE' showed up in a real Walmart-Chile
    payslip (Liquidacion_202609.PDF) with no matching concept_code at all --
    unlike the payslip-variant gaps above, this was a genuinely new domain
    concept (CCAF_LOAN), added to pf-db's PAY_CONCEPT seed data together with
    this template field. The pattern is deliberately not anchored to 'LA
    ARAUCANA' specifically: an employee affiliated with a different CCAF
    (Los Andes, Los Heroes, 18 de Septiembre) gets the same '<CCAF NAME>
    VIGENTE' line shape.
    """

    @pytest.mark.parametrize(
        "raw_label",
        [
            "CCAF LA ARAUCANA VIGENTE",
            "CCAF LOS ANDES VIGENTE",
        ],
    )
    def test_ccaf_vigente_line_maps_to_ccaf_loan(self, raw_label: str) -> None:
        """Any '<CCAF NAME> VIGENTE' label must resolve to CCAF_LOAN."""
        template = _load_real_shipped_template()
        field = match_field(template, raw_label)
        assert field is not None
        assert field.concept_code == "CCAF_LOAN"
        assert field.kind == "discount"
