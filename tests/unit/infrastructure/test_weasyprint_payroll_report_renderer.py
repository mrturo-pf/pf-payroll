"""Tests for test weasyprint payroll report renderer."""

import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from payroll.application.dto import (
    PayrollItemDetailDTO,
    PayrollPeriodDetailDTO,
    PayrollSummaryDTO,
)
from payroll.infrastructure.reporting.weasyprint_payroll_report_renderer import (
    WeasyPrintPayrollReportRenderer,
    _build_pdf,
    _escape_pdf_text,
    _format_clp,
)
from tests.helpers.reference_data import sample_payroll_period_detail_dto


def sample_detail() -> PayrollPeriodDetailDTO:
    """Sample detail."""
    return sample_payroll_period_detail_dto(
        10,
        employer_name="ACME & Co",
        employer_tax_id="76.123.456-7",
        status="reviewed",
        items=[
            PayrollItemDetailDTO(
                concept_code="SALARY_BASE",
                concept_name="Base Salary",
                kind="income",
                is_taxable=True,
                amount_clp=Decimal("1000000"),
                notes=None,
            ),
            PayrollItemDetailDTO(
                concept_code="INCOME_TAX",
                concept_name="Income Tax",
                kind="discount",
                is_taxable=False,
                amount_clp=Decimal("0"),
                notes="computed",
            ),
        ],
        summary=PayrollSummaryDTO(
            period_id=10,
            employer_id=1,
            employer_name="ACME & Co",
            period_year=2026,
            period_month=1,
            payment_date=date(2026, 1, 31),
            taxable_income_clp=Decimal("1000000"),
            gross_income_clp=Decimal("1000000"),
            total_discounts_clp=Decimal("176000"),
            net_pay_clp=Decimal("824000"),
        ),
    )


def test_format_clp_uses_chilean_thousands_separator() -> None:
    """Test format clp uses chilean thousands separator."""
    assert _format_clp(Decimal("1234567")) == "$1.234.567"


def test_escape_pdf_text_escapes_reserved_characters() -> None:
    """Test escape pdf text escapes reserved characters."""
    assert _escape_pdf_text(r"ACME (Chile)\Ops") == r"ACME \(Chile\)\\Ops"


def test_build_pdf_returns_pdf_bytes() -> None:
    """Test build pdf returns pdf bytes."""
    assert _build_pdf(["hello"]).startswith(b"%PDF")


def test_weasyprint_payroll_report_renderer_returns_pdf_bytes_without_native_libs() -> (
    None
):
    """Test weasyprint payroll report renderer returns pdf bytes without native libs."""
    pdf = WeasyPrintPayrollReportRenderer().render_payroll_period(sample_detail())

    assert pdf.startswith(b"%PDF")


def test_weasyprint_payroll_report_renderer_uses_weasyprint_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test weasyprint payroll report renderer uses weasyprint when available."""

    class FakeHTML:
        """Test double for HTML."""

        def __init__(self, string: str) -> None:
            """Initialize the instance."""
            self.string = string

        def write_pdf(self) -> bytes:
            """Write pdf."""
            assert "Payroll Period Report" in self.string
            return b"%PDF-weasy"

    fake_module = type("FakeWeasyPrintModule", (), {"HTML": FakeHTML})()
    monkeypatch.setitem(sys.modules, "weasyprint", fake_module)

    pdf = WeasyPrintPayrollReportRenderer().render_payroll_period(sample_detail())

    assert pdf == b"%PDF-weasy"


def test_weasyprint_payroll_report_renderer_requires_summary() -> None:
    """Test weasyprint payroll report renderer requires summary."""
    with pytest.raises(
        ValueError, match="Payroll summary for period 10 was not found."
    ):
        WeasyPrintPayrollReportRenderer().render_payroll_period(
            replace(sample_detail(), summary=None)
        )
