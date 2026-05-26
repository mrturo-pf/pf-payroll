"""Tests for test xlsx importer."""

from datetime import date, datetime
from io import BytesIO
from decimal import Decimal

import pandas as pd
import pytest

from payroll.infrastructure.importers.xlsx_importer import (
    XlsxPayrollImporter,
    extract_net_pay_validations,
    parse_period,
    parse_optional_health_plan_ids,
    parse_optional_plan_id,
    parse_payment_date,
    parse_worked_days,
    read_payroll_dataframe,
    to_long_format,
)


def test_read_payroll_dataframe_supports_csv_and_xlsx() -> None:
    """Test read payroll dataframe supports csv and xlsx."""
    csv_frame = read_payroll_dataframe(
        "sample.csv",
        BytesIO(
            b"period_month,period_year,employer,payment_date,employment_contract_kind,salary_base\n"
            b"1,2026,ACME,2026-01-31,indefinite,1000\n"
        ),
    )

    xlsx_payload = BytesIO()
    pd.DataFrame(
        [
            {
                "period_month": 1,
                "period_year": 2026,
                "employer": "ACME",
                "payment_date": "2026-01-31",
                "employment_contract_kind": "indefinite",
                "salary_base": 1000,
            }
        ]
    ).to_excel(xlsx_payload, index=False)
    xlsx_payload.seek(0)
    xlsx_frame = read_payroll_dataframe("sample.xlsx", xlsx_payload)

    assert csv_frame.iloc[0]["employer"] == "ACME"
    assert xlsx_frame.iloc[0]["employer"] == "ACME"


def test_read_payroll_dataframe_rejects_unknown_extensions() -> None:
    """Test read payroll dataframe rejects unknown extensions."""
    with pytest.raises(ValueError, match="Unsupported payroll file format"):
        read_payroll_dataframe("sample.txt", BytesIO(b"noop"))


def test_to_long_format_skips_invalid_period_and_validates_required_fields() -> None:
    """Test to long format skips invalid period and validates required fields."""
    result = to_long_format(
        pd.DataFrame(
            [
                {
                    "period_month": "",
                    "period_year": "",
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                }
            ]
        )
    )
    assert result.empty

    result = to_long_format(
        pd.DataFrame(
            [
                {
                    "period_month": 1,
                    "period_year": 2026,
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "worked_days": 28,
                    "salary_base": 1000,
                }
            ]
        )
    )
    assert result.iloc[0]["worked_days"] == 28

    with pytest.raises(ValueError, match="payment_date"):
        to_long_format(
            pd.DataFrame(
                [
                    {
                        "period_month": 1,
                        "period_year": 2026,
                        "employer": "ACME",
                        "employment_contract_kind": "indefinite",
                    }
                ]
            )
        )

    with pytest.raises(ValueError, match="employer"):
        to_long_format(
            pd.DataFrame(
                [
                    {
                        "period_month": 1,
                        "period_year": 2026,
                        "payment_date": "2026-01-31",
                        "employment_contract_kind": "indefinite",
                    }
                ]
            )
        )

    with pytest.raises(ValueError, match="employment_contract_kind"):
        to_long_format(
            pd.DataFrame(
                [
                    {
                        "period_month": 1,
                        "period_year": 2026,
                        "employer": "ACME",
                        "payment_date": "2026-01-31",
                    }
                ]
            )
        )

    with pytest.raises(ValueError, match="worked_days"):
        to_long_format(
            pd.DataFrame(
                [
                    {
                        "period_month": 1,
                        "period_year": 2026,
                        "employer": "ACME",
                        "payment_date": "2026-01-31",
                        "employment_contract_kind": "indefinite",
                        "worked_days": 31.5,
                        "salary_base": 1000,
                    }
                ]
            )
        )

    with pytest.raises(ValueError, match="Both period_month and period_year"):
        to_long_format(
            pd.DataFrame(
                [
                    {
                        "period_month": 1,
                        "employer": "ACME",
                        "payment_date": "2026-01-31",
                        "employment_contract_kind": "indefinite",
                        "salary_base": 1000,
                    }
                ]
            )
        )

    with pytest.raises(ValueError, match="period_month"):
        to_long_format(
            pd.DataFrame(
                [
                    {
                        "period_month": 13,
                        "period_year": 2026,
                        "employer": "ACME",
                        "payment_date": "2026-01-31",
                        "employment_contract_kind": "indefinite",
                        "salary_base": 1000,
                    }
                ]
            )
        )


def test_to_long_format_normalizes_contract_kind_aliases() -> None:
    """Test to long format normalizes contract kind aliases."""
    result = to_long_format(
        pd.DataFrame(
            [
                {
                    "period_month": 1,
                    "period_year": 2026,
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "plazo_fijo",
                    "salary_base": 1000,
                }
            ]
        )
    )

    assert (
        result.to_dict(orient="records")[0]["employment_contract_kind"].value
        == "fixed_term"
    )


def test_to_long_format_rejects_invalid_contract_kind() -> None:
    """Test to long format rejects invalid contract kind."""
    with pytest.raises(ValueError, match="Unsupported employment_contract_kind"):
        to_long_format(
            pd.DataFrame(
                [
                    {
                        "period_month": 1,
                        "period_year": 2026,
                        "employer": "ACME",
                        "payment_date": "2026-01-31",
                        "employment_contract_kind": "seasonal",
                        "salary_base": 1000,
                    }
                ]
            )
        )


def test_parse_payment_date_supports_iso_and_dayfirst_formats() -> None:
    """Test parse payment date supports iso and dayfirst formats."""
    assert str(parse_payment_date("2026-01-31").date()) == "2026-01-31"
    assert str(parse_payment_date("31/01/2026").date()) == "2026-01-31"
    assert str(parse_payment_date(pd.Timestamp("2026-01-31")).date()) == "2026-01-31"
    assert str(parse_payment_date(datetime(2026, 1, 31, 8, 30)).date()) == "2026-01-31"
    assert str(parse_payment_date(date(2026, 1, 31)).date()) == "2026-01-31"


def test_parse_worked_days_defaults_and_validates() -> None:
    """Test parse worked days defaults and validates."""
    assert parse_worked_days(None) == 30
    assert parse_worked_days("") == 30
    assert parse_worked_days("28") == 28

    with pytest.raises(ValueError, match="worked_days"):
        parse_worked_days("31.5")

    with pytest.raises(ValueError, match="worked_days"):
        parse_worked_days("32")

    with pytest.raises(ValueError, match="worked_days"):
        parse_worked_days("abc")


def test_parse_period_prefers_split_fields_and_validates_inputs() -> None:
    """Test parse period handles split fields and validates values."""
    assert parse_period(pd.Series({"period_month": 1, "period_year": 2026})) == (
        1,
        2026,
    )
    assert parse_period(pd.Series({"period_month": "", "period_year": ""})) is None
    assert (
        parse_period(pd.Series({"period_month": pd.NA, "period_year": pd.NA})) is None
    )

    with pytest.raises(ValueError, match="Both period_month and period_year"):
        parse_period(pd.Series({"period_month": 1, "period_year": None}))

    with pytest.raises(ValueError, match="period_month"):
        parse_period(pd.Series({"period_month": "abc", "period_year": 2026}))

    with pytest.raises(ValueError, match="period_month"):
        parse_period(pd.Series({"period_month": "1.5", "period_year": 2026}))

    with pytest.raises(ValueError, match="period_year"):
        parse_period(pd.Series({"period_month": 1, "period_year": "abc"}))

    with pytest.raises(ValueError, match="period_year"):
        parse_period(pd.Series({"period_month": 1, "period_year": 0}))


def test_parse_optional_plan_id_defaults_and_validates() -> None:
    """Test parse optional plan id defaults and validates."""
    assert parse_optional_plan_id("pension_plan_id", None) is None
    assert parse_optional_plan_id("pension_plan_id", "") is None
    assert parse_optional_plan_id("pension_plan_id", "3") == 3

    with pytest.raises(ValueError, match="pension_plan_id"):
        parse_optional_plan_id("pension_plan_id", "0")

    with pytest.raises(ValueError, match="pension_plan_id"):
        parse_optional_plan_id("pension_plan_id", "3.5")

    with pytest.raises(ValueError, match="pension_plan_id"):
        parse_optional_plan_id("pension_plan_id", "abc")


def test_parse_optional_health_plan_ids_defaults_and_validates() -> None:
    """Test parse optional health plan ids defaults and validates."""
    assert parse_optional_health_plan_ids(None) is None
    assert parse_optional_health_plan_ids("") is None
    assert parse_optional_health_plan_ids("2") == (2,)
    assert parse_optional_health_plan_ids("2,3") == (2, 3)
    assert parse_optional_health_plan_ids("2|3;4/5") == (2, 3, 4, 5)
    assert parse_optional_health_plan_ids(" , ; / ") is None

    with pytest.raises(ValueError, match="health_plan_id"):
        parse_optional_health_plan_ids("2,a")


def test_extract_net_pay_validations_returns_expected_and_difference_values() -> None:
    """Test extract net pay validations returns expected and difference values."""
    result = extract_net_pay_validations(
        pd.DataFrame(
            [
                {
                    "period_month": "",
                    "period_year": "",
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                    "net_pay": 1000,
                },
                {
                    "period_month": 1,
                    "period_year": 2026,
                    "employer": "",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                    "net_pay": 1000,
                },
                {
                    "period_month": 1,
                    "period_year": 2026,
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                },
                {
                    "period_month": 1,
                    "period_year": 2026,
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                    "pension_base": 100,
                    "net_pay": 950,
                },
                {
                    "period_month": 2,
                    "period_year": 2026,
                    "employer": "ACME",
                    "payment_date": "2026-02-28",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                    "pension_base": 100,
                    "net_pay": 900,
                },
            ]
        )
    )

    assert result[("ACME", 2026, 1)].declared_net_pay_clp == Decimal("950")
    assert result[("ACME", 2026, 1)].expected_net_pay_clp == Decimal("900")
    assert result[("ACME", 2026, 1)].net_pay_difference_clp == Decimal("50")
    assert result[("ACME", 2026, 2)].declared_net_pay_clp == Decimal("900")
    assert result[("ACME", 2026, 2)].expected_net_pay_clp == Decimal("900")
    assert result[("ACME", 2026, 2)].net_pay_difference_clp == Decimal("0")
    assert result[("ACME", 2026, 2)].warning is None


def test_xlsx_payroll_importer_builds_application_rows() -> None:
    """Test xlsx payroll importer builds application rows."""
    rows = XlsxPayrollImporter().read_rows(
        "sample.csv",
        (
            b"period_month,period_year,employer,payment_date,worked_days,employment_contract_kind,"
            b"pension_plan_id,health_plan_id,salary_base,pension_base,net_pay\n"
            b"1,2026,ACME,31/01/2026,28,indefinite,1,2,1000000,100000,950000\n"
        ),
    )

    assert len(rows) == 2
    assert rows[0].payment_date.isoformat() == "2026-01-31"
    assert rows[0].worked_days == 28
    assert rows[0].pension_plan_id == 1
    assert rows[0].health_plan_id == 2
    assert rows[0].health_plan_ids == (2,)
    assert rows[0].declared_net_pay_clp == Decimal("950000")
    assert rows[0].expected_net_pay_clp == Decimal("900000")
    assert rows[0].net_pay_difference_clp == Decimal("50000")


def test_xlsx_payroll_importer_maps_health_plan_additional_column() -> None:
    """Test importer maps the health plan additional input column."""
    rows = XlsxPayrollImporter().read_rows(
        "sample.csv",
        (
            b"period_month,period_year,employer,payment_date,employment_contract_kind,"
            b"health_plan_additional,net_pay\n"
            b"1,2026,ACME,31/01/2026,indefinite,87500,0\n"
        ),
    )

    assert len(rows) == 1
    assert rows[0].concept_code == "HEALTH_ADDITIONAL_UF"
    assert rows[0].amount_clp == Decimal("87500")


def test_xlsx_payroll_importer_reads_multiple_health_plan_ids() -> None:
    """Test importer parses multiple health plans from a single CSV column."""
    rows = XlsxPayrollImporter().read_rows(
        "sample.csv",
        (
            b"period_month,period_year,employer,payment_date,employment_contract_kind,"
            b"pension_plan_id,health_plan_id,salary_base,net_pay\n"
            b'1,2026,ACME,31/01/2026,indefinite,1,"2,3",1000000,1000000\n'
        ),
    )

    assert rows[0].pension_plan_id == 1
    assert rows[0].health_plan_id == 2
    assert rows[0].health_plan_ids == (2, 3)


def test_xlsx_payroll_importer_maps_health_insurance_columns() -> None:
    """Test importer maps health-insurance income and discount columns."""
    rows = XlsxPayrollImporter().read_rows(
        "sample.csv",
        (
            b"period_month,period_year,employer,payment_date,employment_contract_kind,"
            b"health_insurance_employer_contribution,health_insurance,net_pay\n"
            b"1,2026,ACME,31/01/2026,indefinite,10030,46139,-36109\n"
        ),
    )

    assert len(rows) == 2
    assert rows[0].concept_code == "HEALTH_INSURANCE_EMPLOYER_CONTRIBUTION"
    assert rows[0].amount_clp == Decimal("10030")
    assert rows[0].expected_net_pay_clp == Decimal("-36109")
    assert rows[1].concept_code == "HEALTH_INSURANCE"
    assert rows[1].amount_clp == Decimal("46139")


def test_xlsx_payroll_importer_maps_additional_taxable_income_columns() -> None:
    """Test importer maps additional taxable income columns."""
    rows = XlsxPayrollImporter().read_rows(
        "sample.csv",
        (
            b"period_month,period_year,employer,payment_date,employment_contract_kind,"
            b"vacation_incentive,holiday_bonus,availability_bonus,"
            b"legal_gratuity_adjustment,prior_salary_difference,net_pay\n"
            b"1,2026,ACME,31/01/2026,indefinite,1000,2000,3000,4000,5000,15000\n"
        ),
    )

    assert [row.concept_code for row in rows] == [
        "VACATION_INCENTIVE",
        "HOLIDAY_BONUS",
        "AVAILABILITY_BONUS",
        "LEGAL_GRATUITY_ADJUSTMENT",
        "PRIOR_SALARY_DIFFERENCE",
    ]
    assert all(row.amount_clp > Decimal("0") for row in rows)
    assert all(row.expected_net_pay_clp == Decimal("15000") for row in rows)


def test_xlsx_payroll_importer_maps_prior_month_leave_absence_discount() -> None:
    """Test importer maps the prior-month leave or absence discount column."""
    rows = XlsxPayrollImporter().read_rows(
        "sample.csv",
        (
            b"period_month,period_year,employer,payment_date,employment_contract_kind,"
            b"prior_month_leave_absence_discount,net_pay\n"
            b"1,2026,ACME,31/01/2026,indefinite,2933,-2933\n"
        ),
    )

    assert len(rows) == 1
    assert rows[0].concept_code == "PRIOR_MONTH_LEAVE_ABSENCE_DISCOUNT"
    assert rows[0].amount_clp == Decimal("2933")


def test_xlsx_payroll_importer_maps_bonus_advance_discount_columns() -> None:
    """Test importer maps bonus advance discount columns."""
    rows = XlsxPayrollImporter().read_rows(
        "sample.csv",
        (
            b"period_month,period_year,employer,payment_date,employment_contract_kind,"
            b"vacation_bonus_advance,holiday_bonus_advance,salary_advance,net_pay\n"
            b"1,2026,ACME,31/01/2026,indefinite,1000,2000,4000,-7000\n"
        ),
    )

    assert [row.concept_code for row in rows] == [
        "VACATION_BONUS_ADVANCE",
        "HOLIDAY_BONUS_ADVANCE",
        "SALARY_ADVANCE",
    ]
    assert all(row.amount_clp > Decimal("0") for row in rows)
    assert all(row.expected_net_pay_clp == Decimal("-7000") for row in rows)
