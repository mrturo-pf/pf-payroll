from io import BytesIO

import pandas as pd
import pytest

from payroll.infrastructure.importers.xlsx_importer import parse_payment_date, read_payroll_dataframe, to_long_format


def test_read_payroll_dataframe_supports_csv_and_xlsx() -> None:
    csv_frame = read_payroll_dataframe(
        "sample.csv",
        BytesIO(b"period,employer,payment_date,salary_base\nJan/2026,ACME,2026-01-31,1000\n"),
    )

    xlsx_payload = BytesIO()
    pd.DataFrame(
        [{"period": "Jan/2026", "employer": "ACME", "payment_date": "2026-01-31", "salary_base": 1000}]
    ).to_excel(xlsx_payload, index=False)
    xlsx_payload.seek(0)
    xlsx_frame = read_payroll_dataframe("sample.xlsx", xlsx_payload)

    assert csv_frame.iloc[0]["employer"] == "ACME"
    assert xlsx_frame.iloc[0]["employer"] == "ACME"


def test_read_payroll_dataframe_rejects_unknown_extensions() -> None:
    with pytest.raises(ValueError, match="Unsupported payroll file format"):
        read_payroll_dataframe("sample.txt", BytesIO(b"noop"))


def test_to_long_format_skips_invalid_period_and_validates_required_fields() -> None:
    result = to_long_format(
        pd.DataFrame([{"period": "2026-01", "employer": "ACME", "payment_date": "2026-01-31", "salary_base": 1000}])
    )
    assert result.empty

    with pytest.raises(ValueError, match="payment_date"):
        to_long_format(pd.DataFrame([{"period": "Jan/2026", "employer": "ACME"}]))

    with pytest.raises(ValueError, match="employer"):
        to_long_format(pd.DataFrame([{"period": "Jan/2026", "payment_date": "2026-01-31"}]))


def test_parse_payment_date_supports_iso_and_dayfirst_formats() -> None:
    assert str(parse_payment_date("2026-01-31").date()) == "2026-01-31"
    assert str(parse_payment_date("31/01/2026").date()) == "2026-01-31"
