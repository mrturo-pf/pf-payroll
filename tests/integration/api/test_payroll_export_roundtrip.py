"""Round-trip tests: export a persisted period, re-import it, assert fidelity.

This is the acceptance criterion the design brief cares about most: "export
a period, re-import that exact file with zero manual edits, and it must
succeed end to end." It is exercised here at the exporter/importer boundary
-- real CsvPayrollExporter/XlsxPayrollExporter (real pandas serialization)
feeding real XlsxPayrollImporter.read_rows() (real pandas deserialization +
CONCEPT_MAP) -- rather than against a live database.

Why not a live-Postgres round trip (testcontainers, mirroring
tests/integration/infrastructure/test_transactional_session.py): that would
also require pf-db's migrations plus seeded reference data (PAY_CONCEPT,
pension/health institutions and plans, an employer) just to construct one
persistable period -- a much bigger lift than this feature's own scope, and
cross-repo by nature (pf-db owns that schema/seed data). The real, permanent
risk this feature introduces -- CONCEPT_MAP (or the wide-format column set)
drifting between the import and export sides -- lives entirely in this
boundary, not in PayrollRepository's pre-existing (and separately tested)
upsert semantics for import_rows(). See
docs/proposals/spreadsheet-export-design-plan.md for the full reasoning.
"""

from datetime import date
from decimal import Decimal

import pytest

from helpers.period_detail_builders import build_acme_period_detail
from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.domain.contributions import EmploymentContractKind
from payroll.infrastructure.exporters.spreadsheet_exporter import (
    CsvPayrollExporter,
    XlsxPayrollExporter,
    export_columns,
)
from payroll.infrastructure.importers.xlsx_importer import XlsxPayrollImporter

# One concept per CONCEPT_MAP kind/is_taxable combination actually declared,
# plus both computed-only concepts -- close enough to a real multi-concept
# payslip to exercise every branch of the inverted mapping, without needing
# all 18 CONCEPT_MAP entries populated.
_DECLARED_CONCEPTS = (
    ("SALARY_BASE", "income", True, Decimal("1000000")),
    ("LEGAL_GRATUITY", "income", True, Decimal("250000")),
    ("TELEWORK_REFUND", "income", False, Decimal("50000")),
    ("PENSION_BASE", "discount", False, Decimal("100000")),
    ("PENSION_ADDITIONAL", "discount", False, Decimal("25000")),
    ("HEALTH_BASE", "discount", False, Decimal("70000")),
    ("HEALTH_ADDITIONAL_UF", "discount", False, Decimal("87500")),
    ("HEALTH_INSURANCE", "discount", False, Decimal("12000")),
    ("PRIOR_MONTH_LEAVE_ABSENCE_DISCOUNT", "discount", False, Decimal("3000")),
)
_COMPUTED_ONLY_CONCEPTS = (
    ("INCOME_TAX", "discount", False, Decimal("45000")),
    ("UNEMPLOYMENT_INSURANCE", "discount", False, Decimal("6000")),
)


def _build_computed_period(*, declared_net_pay_clp: Decimal) -> PayrollPeriodDetailDTO:
    """Build a synthetic-but-realistic period that has gone through full processing."""
    items = [
        PayrollItemDetailDTO(code, code.title(), kind, is_taxable, amount, None)
        for code, kind, is_taxable, amount in (
            *_DECLARED_CONCEPTS,
            *_COMPUTED_ONLY_CONCEPTS,
        )
    ]
    summary = PayrollSummaryDTO(
        period_id=1,
        employer_id=1,
        employer_name="ACME",
        period_year=2026,
        period_month=1,
        payment_date=date(2026, 1, 31),
        taxable_income_clp=Decimal("1000000"),
        gross_income_clp=Decimal("1300000"),
        total_discounts_clp=Decimal("195000"),
        net_pay_clp=declared_net_pay_clp,
        declared_net_pay_clp=declared_net_pay_clp,
    )
    return build_acme_period_detail(items=items, summary=summary)


@pytest.mark.parametrize(
    ("exporter_cls", "filename"),
    [(CsvPayrollExporter, "export.csv"), (XlsxPayrollExporter, "export.xlsx")],
)
def test_reimporting_an_export_reconstructs_every_declared_concept(
    exporter_cls: type, filename: str
) -> None:
    """Every declared (non-computed) concept survives export -> reimport exactly."""
    detail = _build_computed_period(declared_net_pay_clp=Decimal("1105000"))
    content = exporter_cls().export([detail])

    rows = XlsxPayrollImporter().read_rows(filename, content)

    assert len(rows) == len(_DECLARED_CONCEPTS)
    reimported_by_code = {row.concept_code: row.amount_clp for row in rows}
    for code, _kind, _is_taxable, amount in _DECLARED_CONCEPTS:
        assert reimported_by_code[code] == amount


@pytest.mark.parametrize(
    ("exporter_cls", "filename"),
    [(CsvPayrollExporter, "export.csv"), (XlsxPayrollExporter, "export.xlsx")],
)
def test_reimporting_an_export_ignores_computed_only_concepts(
    exporter_cls: type, filename: str
) -> None:
    """INCOME_TAX / UNEMPLOYMENT_INSURANCE never reappear as importable rows.

    Proves the "ignore, don't reject or persist" decision from the design
    recommendation (Item 1) holds for real: those two concepts are present
    in the source PayrollPeriodDetailDTO and in the exported file's header,
    but the importer -- which only ever reads CONCEPT_MAP columns -- never
    produces a row for them.
    """
    detail = _build_computed_period(declared_net_pay_clp=Decimal("1105000"))
    content = exporter_cls().export([detail])

    rows = XlsxPayrollImporter().read_rows(filename, content)

    reimported_codes = {row.concept_code for row in rows}
    assert "INCOME_TAX" not in reimported_codes
    assert "UNEMPLOYMENT_INSURANCE" not in reimported_codes


@pytest.mark.parametrize(
    ("exporter_cls", "filename"),
    [(CsvPayrollExporter, "export.csv"), (XlsxPayrollExporter, "export.xlsx")],
)
def test_reimporting_an_export_preserves_period_header_fields(
    exporter_cls: type, filename: str
) -> None:
    """employer/period/payment_date/worked_days/contract kind survive the round trip."""
    detail = _build_computed_period(declared_net_pay_clp=Decimal("1105000"))
    content = exporter_cls().export([detail])

    rows = XlsxPayrollImporter().read_rows(filename, content)

    assert {row.employer for row in rows} == {"ACME"}
    assert {row.period_year for row in rows} == {2026}
    assert {row.period_month for row in rows} == {1}
    assert {row.payment_date for row in rows} == {date(2026, 1, 31)}
    assert {row.worked_days for row in rows} == {30}
    assert {row.employment_contract_kind for row in rows} == {
        EmploymentContractKind.INDEFINITE
    }


@pytest.mark.parametrize(
    ("exporter_cls", "filename"),
    [(CsvPayrollExporter, "export.csv"), (XlsxPayrollExporter, "export.xlsx")],
)
def test_reimport_net_pay_difference_equals_exactly_the_computed_only_total(
    exporter_cls: type, filename: str
) -> None:
    """The only "difference" a re-import can ever see is the computed-only total.

    The importer's expected_net_pay_clp is (by construction, see
    xlsx_importer.parse_period) a sum over CONCEPT_MAP columns alone -- it
    has no column for INCOME_TAX/UNEMPLOYMENT_INSURANCE and never will (see
    Item 1 of the design recommendation). So a period whose *true* declared
    net pay already accounts for those two computed deductions will always
    show net_pay_difference_clp == -(income_tax + unemployment_insurance)
    on any import, first or re-import alike -- this is pre-existing,
    expected behavior that export/re-import does not need to eliminate.
    What this test actually proves: re-importing an export reproduces
    *exactly* that pre-existing difference, with zero additional drift
    introduced by the export/re-import round trip itself.
    """
    declared_income = sum(
        amount
        for _code, kind, _taxable, amount in _DECLARED_CONCEPTS
        if kind == "income"
    )
    declared_discounts = sum(
        amount
        for _code, kind, _taxable, amount in _DECLARED_CONCEPTS
        if kind == "discount"
    )
    computed_only_total = sum(
        amount for _code, _kind, _taxable, amount in _COMPUTED_ONLY_CONCEPTS
    )
    expected_from_declared_columns = declared_income - declared_discounts
    true_declared_net_pay = expected_from_declared_columns - computed_only_total

    detail = _build_computed_period(declared_net_pay_clp=true_declared_net_pay)
    content = exporter_cls().export([detail])

    rows = XlsxPayrollImporter().read_rows(filename, content)

    for row in rows:
        assert row.declared_net_pay_clp == true_declared_net_pay
        assert row.expected_net_pay_clp == expected_from_declared_columns
        assert row.net_pay_difference_clp == -computed_only_total


def test_export_header_column_count_matches_wide_columns_plus_computed() -> None:
    """Fails loudly if CONCEPT_MAP grows without the exporter's column set following.

    Per Item 5's own requirement: this assertion is computed from the app's
    own export_columns() helper, not a hardcoded number, so it only ever
    fails when the two sides of the pipeline (import vs. export) genuinely
    stop agreeing on the column shape.
    """
    detail = _build_computed_period(declared_net_pay_clp=Decimal("1105000"))
    csv_bytes = CsvPayrollExporter().export([detail])
    header = csv_bytes.decode().splitlines()[0].split(",")

    assert header == export_columns()
