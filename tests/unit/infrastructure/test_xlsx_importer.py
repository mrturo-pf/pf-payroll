"""Tests for test xlsx importer."""

from datetime import date, datetime
from io import BytesIO
from decimal import Decimal

import pandas as pd
import pytest

from payroll.infrastructure.importers.xlsx_importer import (
    XlsxPayrollImporter,
    extract_net_pay_validations,
    parse_payment_date,
    read_payroll_dataframe,
    to_long_format,
)


def test_read_payroll_dataframe_supports_csv_and_xlsx() -> None:
    """Test read payroll dataframe supports csv and xlsx."""
    csv_frame = read_payroll_dataframe(
        "sample.csv",
        BytesIO(
            b"period,employer,payment_date,employment_contract_kind,salary_base\n"
            b"Jan/2026,ACME,2026-01-31,indefinite,1000\n"
        ),
    )

    xlsx_payload = BytesIO()
    pd.DataFrame(
        [
            {
                "period": "Jan/2026",
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
                    "period": "2026-01",
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                }
            ]
        )
    )
    assert result.empty

    with pytest.raises(ValueError, match="payment_date"):
        to_long_format(pd.DataFrame([{"period": "Jan/2026", "employer": "ACME", "employment_contract_kind": "indefinite"}]))

    with pytest.raises(ValueError, match="employer"):
        to_long_format(
            pd.DataFrame([{"period": "Jan/2026", "payment_date": "2026-01-31", "employment_contract_kind": "indefinite"}])
        )

    with pytest.raises(ValueError, match="employment_contract_kind"):
        to_long_format(pd.DataFrame([{"period": "Jan/2026", "employer": "ACME", "payment_date": "2026-01-31"}]))


def test_to_long_format_normalizes_contract_kind_aliases() -> None:
    """Test to long format normalizes contract kind aliases."""
    result = to_long_format(
        pd.DataFrame(
            [
                {
                    "period": "Jan/2026",
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "plazo_fijo",
                    "salary_base": 1000,
                }
            ]
        )
    )

    assert result.to_dict(orient="records")[0]["employment_contract_kind"].value == "fixed_term"


def test_to_long_format_rejects_invalid_contract_kind() -> None:
    """Test to long format rejects invalid contract kind."""
    with pytest.raises(ValueError, match="Unsupported employment_contract_kind"):
        to_long_format(
            pd.DataFrame(
                [
                    {
                        "period": "Jan/2026",
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


def test_extract_net_pay_validations_returns_expected_and_difference_values() -> None:
    """Test extract net pay validations returns expected and difference values."""
    result = extract_net_pay_validations(
        pd.DataFrame(
            [
                {
                    "period": "2026-01",
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                    "net_pay": 1000,
                },
                {
                    "period": "Jan/2026",
                    "employer": "",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                    "net_pay": 1000,
                },
                {
                    "period": "Jan/2026",
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                },
                {
                    "period": "Jan/2026",
                    "employer": "ACME",
                    "payment_date": "2026-01-31",
                    "employment_contract_kind": "indefinite",
                    "salary_base": 1000,
                    "pension_base": 100,
                    "net_pay": 950,
                },
                {
                    "period": "Feb/2026",
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
            b"period,employer,payment_date,employment_contract_kind,salary_base,pension_base,net_pay\n"
            b"Jan/2026,ACME,31/01/2026,indefinite,1000000,100000,950000\n"
        ),
    )

    assert len(rows) == 2
    assert rows[0].payment_date.isoformat() == "2026-01-31"
    assert rows[0].declared_net_pay_clp == Decimal("950000")
    assert rows[0].expected_net_pay_clp == Decimal("900000")
    assert rows[0].net_pay_difference_clp == Decimal("50000")
