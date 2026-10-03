"""Tests for the payroll spreadsheet export adapters."""

from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from helpers.period_detail_builders import build_acme_period_detail
from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.infrastructure.exporters.spreadsheet_exporter import (
    MONEY_NUMBER_FORMAT,
    CsvPayrollExporter,
    XlsxPayrollExporter,
    build_export_dataframe,
    build_template_dataframe,
    export_columns,
    get_exporter_for_format,
)
from payroll.infrastructure.importers.xlsx_importer import (
    CONCEPT_MAP,
    COMPUTED_ONLY_CONCEPT_COLUMNS,
    wide_columns,
)


def _build_detail(
    *, include_computed: bool = True, net_pay: Decimal | None = Decimal("1105000")
) -> PayrollPeriodDetailDTO:
    """Build a realistic (synthetic) persisted period for export tests."""
    items = [
        PayrollItemDetailDTO(
            "SALARY_BASE", "Base", "income", True, Decimal("1000000"), None
        ),
        PayrollItemDetailDTO(
            "LEGAL_GRATUITY", "Gratuity", "income", True, Decimal("250000"), None
        ),
        PayrollItemDetailDTO(
            "PENSION_BASE", "Pension", "discount", False, Decimal("100000"), None
        ),
    ]
    if include_computed:
        items.append(
            PayrollItemDetailDTO(
                "INCOME_TAX", "Income Tax", "discount", False, Decimal("45000"), None
            )
        )
        items.append(
            PayrollItemDetailDTO(
                "UNEMPLOYMENT_INSURANCE",
                "Unemployment",
                "discount",
                False,
                Decimal("6000"),
                None,
            )
        )
    summary = (
        None
        if net_pay is None
        else PayrollSummaryDTO(
            period_id=1,
            employer_id=1,
            employer_name="ACME",
            period_year=2026,
            period_month=1,
            payment_date=date(2026, 1, 31),
            taxable_income_clp=Decimal("1000000"),
            gross_income_clp=Decimal("1250000"),
            total_discounts_clp=Decimal("145000"),
            net_pay_clp=Decimal("1105000"),
            declared_net_pay_clp=net_pay,
        )
    )
    return build_acme_period_detail(items=items, summary=summary)


def test_wide_columns_matches_concept_map_count() -> None:
    """Guard against CONCEPT_MAP drift: prefix + concepts + net_pay, in order."""
    columns = wide_columns()
    assert len(columns) == 5 + len(CONCEPT_MAP) + 1
    assert columns[0] == "period_month"
    assert columns[4] == "worked_days"
    assert columns[-1] == "net_pay"
    assert columns[5 : 5 + len(CONCEPT_MAP)] == list(CONCEPT_MAP.keys())


def test_export_columns_appends_computed_only_columns_after_wide_columns() -> None:
    """The real export's header row is wide_columns() + computed-only, in order."""
    columns = export_columns()
    assert columns[: len(wide_columns())] == wide_columns()
    assert columns[len(wide_columns()) :] == list(
        COMPUTED_ONLY_CONCEPT_COLUMNS.values()
    )


def test_template_dataframe_has_zero_rows_and_no_computed_columns() -> None:
    """The blank template has only the header row, no computed-only columns."""
    dataframe = build_template_dataframe()
    assert list(dataframe.columns) == wide_columns()
    assert len(dataframe) == 0
    assert "income_tax" not in dataframe.columns
    assert "unemployment_insurance" not in dataframe.columns


def test_export_dataframe_uses_same_column_order_as_template_plus_computed() -> None:
    """The real export's columns are the template's columns plus computed-only ones."""
    dataframe = build_export_dataframe([_build_detail()])
    assert list(dataframe.columns) == export_columns()
    assert list(dataframe.columns)[: len(wide_columns())] == list(
        build_template_dataframe().columns
    )


def test_export_dataframe_maps_items_through_inverted_concept_map() -> None:
    """Declared concepts land in their CONCEPT_MAP column; computed in the extras.

    Values stay native Decimal (never str()) so the XLSX writer produces real
    numeric cells instead of text -- see spreadsheet_exporter._to_xlsx_bytes.
    """
    dataframe = build_export_dataframe([_build_detail()])
    row = dataframe.iloc[0]
    assert row["salary_base"] == Decimal("1000000")
    assert row["monthly_legal_gratuity"] == Decimal("250000")
    assert row["pension_base"] == Decimal("100000")
    assert row["income_tax"] == Decimal("45000")
    assert row["unemployment_insurance"] == Decimal("6000")
    assert row["net_pay"] == Decimal("1105000")
    assert row["employer"] == "ACME"


def test_export_dataframe_omits_computed_columns_when_period_has_none() -> None:
    """A period never run through compute-tax/compute-contributions exports blank."""
    dataframe = build_export_dataframe([_build_detail(include_computed=False)])
    row = dataframe.iloc[0]
    assert row.isna()["income_tax"]
    assert row.isna()["unemployment_insurance"]


def test_export_dataframe_leaves_net_pay_blank_without_a_summary() -> None:
    """A period with no PAY_MV_SUMARY row yet exports an empty net_pay cell."""
    dataframe = build_export_dataframe([_build_detail(net_pay=None)])
    assert dataframe.iloc[0]["net_pay"] is None or dataframe.iloc[0].isna()["net_pay"]


def test_csv_exporter_export_and_template_round_trip_through_pandas() -> None:
    """CSV bytes decode back into the same header row(s) that were written."""
    exporter = CsvPayrollExporter()
    template_bytes = exporter.export_template()
    header_line = template_bytes.decode().splitlines()[0]
    assert header_line.split(",") == wide_columns()

    export_bytes = exporter.export([_build_detail()])
    lines = export_bytes.decode().splitlines()
    assert lines[0].split(",") == export_columns()
    assert len(lines) == 2  # header + one data row


def test_xlsx_exporter_produces_nonempty_bytes_for_export_and_template() -> None:
    """XLSX bytes are real, distinct workbook payloads for export vs. template."""
    exporter = XlsxPayrollExporter()
    export_bytes = exporter.export([_build_detail()])
    template_bytes = exporter.export_template()
    assert export_bytes.startswith(b"PK")  # xlsx is a zip container
    assert template_bytes.startswith(b"PK")
    assert export_bytes != template_bytes


def test_export_with_no_matching_periods_still_produces_a_valid_header_only_file() -> (
    None
):
    """An empty periods list (no match) exports a header-only file, not an error."""
    csv_bytes = CsvPayrollExporter().export([])
    assert csv_bytes.decode().splitlines() == [",".join(export_columns())]


def test_xlsx_exporter_writes_money_columns_as_real_numbers() -> None:
    """Regression test: money cells must be numeric ('n'), never text ('s').

    A text cell like "1536167.00" displays literally in any Excel locale
    that expects ',' as the decimal separator -- it won't be recognized as a
    number, won't sum, and won't reformat. Storing a real numeric cell lets
    Excel render the decimal separator per the user's own locale instead.
    """
    xlsx_bytes = XlsxPayrollExporter().export([_build_detail()])

    workbook = load_workbook(BytesIO(xlsx_bytes))
    worksheet = workbook.active
    header = [cell.value for cell in worksheet[1]]
    data_row = {header[i]: cell for i, cell in enumerate(worksheet[2])}

    for column in ("salary_base", "net_pay", "income_tax", "unemployment_insurance"):
        cell = data_row[column]
        assert cell.data_type == "n", (
            f"{column} cell should be numeric, got {cell.data_type!r}"
        )
        assert cell.number_format == MONEY_NUMBER_FORMAT


def test_get_exporter_for_format_resolves_csv_and_xlsx() -> None:
    """Format resolution picks the matching adapter type."""
    assert isinstance(get_exporter_for_format("csv"), CsvPayrollExporter)
    assert isinstance(get_exporter_for_format("xlsx"), XlsxPayrollExporter)


def test_export_dataframe_skips_a_concept_with_no_column_at_all() -> None:
    """An item whose concept_code has no column anywhere is silently dropped.

    Not an expected case today (see design recommendation Item 1 -- every
    persistable concept has a column or is one of the two known
    computed-only ones), but a visible no-op rather than a KeyError if
    PAY_CONCEPT ever grows a new code neither side of this pipeline knows
    about yet -- the CONCEPT_MAP-drift tests in
    tests/integration/api/test_payroll_export_roundtrip.py are what should
    catch that drift, not an exception raised mid-export.
    """
    detail = _build_detail(include_computed=False)
    detail.items.append(
        PayrollItemDetailDTO(
            "SOME_FUTURE_CONCEPT", "?", "discount", False, Decimal("1"), None
        )
    )

    dataframe = build_export_dataframe([detail])

    assert "SOME_FUTURE_CONCEPT" not in dataframe.columns
    assert list(dataframe.columns) == export_columns()
