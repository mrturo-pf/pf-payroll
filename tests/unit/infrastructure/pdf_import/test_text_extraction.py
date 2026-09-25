"""Tests for the employer-agnostic PDF text parsing helpers."""

from decimal import Decimal
from unittest.mock import patch

from payroll.infrastructure.pdf_import.text_extraction import (
    extract_raw_text,
    find_amount_start_column,
    find_detail_lines,
    find_income_discount_column_boundary,
    parse_amount,
    parse_declared_net_pay,
    parse_detail_line,
    parse_header,
    parse_period,
    parse_worked_days,
)

SAMPLE_TEXT = (
    "ACME Corp S.A\n"
    "R.U.T. NOMBRE MES AÑO\n"
    "11.111.111-1 DOE JANE Marzo 2026\n"
    "\n"
    "LUGAR DE TRABAJO ANTIGUEDAD LABORAL DIAS TRABAJADOS\n"
    "S1 01.01.2020 25\n"
    "\n"
    "DETALLE HABERES Y DESCUENTOS   CODIGO  CUOTAS   HABERES   DESCUENTOS\n"
    "                                REMANENTES\n"
    "SUELDO BASE 1000                     1,000,000\n"
    "BONO DESEMPEÑO 1A11                     150,000\n"
    "IMPUESTO UNICO /370                              50,000\n"
    "\n"
    "TOTALES 1,150,000 50,000\n"
    "LIQUIDO  A PAGAR 1,100,000\n"
    "Liquido a pagar UN MILLON CIEN MIL PESOS.\n"
)


class TestExtractRawText:
    """Tests for extract_raw_text."""

    def test_returns_joined_layout_text_from_all_pages(self) -> None:
        """Test returns joined layout text from all pages."""
        fake_page = type(
            "FakePage", (), {"extract_text": lambda self, extraction_mode: "hello"}
        )()
        fake_reader = type("FakeReader", (), {"pages": [fake_page, fake_page]})()
        with patch(
            "payroll.infrastructure.pdf_import.text_extraction.PdfReader",
            return_value=fake_reader,
        ):
            assert extract_raw_text(b"whatever") == "hello\nhello"

    def test_returns_none_when_pdf_reader_raises(self) -> None:
        """Test returns none when pdf reader raises."""
        with patch(
            "payroll.infrastructure.pdf_import.text_extraction.PdfReader",
            side_effect=ValueError("not a pdf"),
        ):
            assert extract_raw_text(b"garbage") is None

    def test_returns_none_when_extracted_text_is_blank(self) -> None:
        """Test returns none when extracted text is blank (e.g. scanned image)."""
        fake_page = type(
            "FakePage", (), {"extract_text": lambda self, extraction_mode: "   "}
        )()
        fake_reader = type("FakeReader", (), {"pages": [fake_page]})()
        with patch(
            "payroll.infrastructure.pdf_import.text_extraction.PdfReader",
            return_value=fake_reader,
        ):
            assert extract_raw_text(b"whatever") is None


class TestParseAmount:
    """Tests for parse_amount."""

    def test_parses_comma_grouped_amount(self) -> None:
        """Test parses comma grouped amount."""
        assert parse_amount("88,000") == Decimal("88000")

    def test_parses_dot_grouped_amount(self) -> None:
        """Test parses dot grouped amount."""
        assert parse_amount("1.234.567") == Decimal("1234567")

    def test_returns_none_for_non_numeric_input(self) -> None:
        """Test returns none for non numeric input."""
        assert parse_amount("abc") is None


class TestParsePeriod:
    """Tests for parse_period."""

    def test_parses_month_name_and_year(self) -> None:
        """Test parses month name and year."""
        assert parse_period(SAMPLE_TEXT) == (2026, 3)

    def test_is_case_insensitive_and_supports_setiembre_alias(self) -> None:
        """Test is case insensitive and supports setiembre alias."""
        assert parse_period("periodo setiembre 2025") == (2025, 9)

    def test_returns_none_when_no_month_year_pair_found(self) -> None:
        """Test returns none when no month year pair found."""
        assert parse_period("no dates here") is None


class TestParseWorkedDays:
    """Tests for parse_worked_days."""

    def test_parses_days_following_antiquity_date(self) -> None:
        """Test parses days following antiquity date."""
        assert parse_worked_days(SAMPLE_TEXT) == 25

    def test_returns_none_when_pattern_missing(self) -> None:
        """Test returns none when pattern missing."""
        assert parse_worked_days("nothing to see here") is None


class TestParseDeclaredNetPay:
    """Tests for parse_declared_net_pay."""

    def test_parses_amount_next_to_liquido_a_pagar(self) -> None:
        """Test parses amount next to liquido a pagar."""
        assert parse_declared_net_pay(SAMPLE_TEXT) == Decimal("1100000")

    def test_returns_none_when_label_missing(self) -> None:
        """Test returns none when label missing."""
        assert parse_declared_net_pay("nothing to see here") is None


class TestParseHeader:
    """Tests for parse_header."""

    def test_parses_all_fields_together(self) -> None:
        """Test parses all fields together."""
        header = parse_header(SAMPLE_TEXT)
        assert header.period_year == 2026
        assert header.period_month == 3
        assert header.worked_days == 25
        assert header.declared_net_pay_clp == Decimal("1100000")

    def test_all_fields_are_none_on_unrecognized_text(self) -> None:
        """Test all fields are none on unrecognized text."""
        header = parse_header("garbage text")
        assert header.period_year is None
        assert header.period_month is None
        assert header.worked_days is None
        assert header.declared_net_pay_clp is None


class TestFindDetailLines:
    """Tests for find_detail_lines."""

    def test_returns_lines_between_detalle_and_totales(self) -> None:
        """Test returns lines between detalle and totales."""
        lines = find_detail_lines(SAMPLE_TEXT)
        assert lines[0].strip() == "REMANENTES"
        assert "SUELDO BASE" in lines[1]
        assert "BONO DESEMPEÑO" in lines[2]
        assert "IMPUESTO UNICO" in lines[3]
        assert len(lines) == 4

    def test_returns_empty_list_when_detalle_marker_missing(self) -> None:
        """Test returns empty list when detalle marker missing."""
        assert find_detail_lines("TOTALES\nsomething") == []

    def test_returns_empty_list_when_totales_marker_missing(self) -> None:
        """Test returns empty list when totales marker missing."""
        assert find_detail_lines("DETALLE\nSUELDO 1000 100,000") == []


class TestParseDetailLine:
    """Tests for parse_detail_line."""

    def test_splits_label_and_amount_stripping_numeric_code(self) -> None:
        """Test splits label and amount stripping numeric code."""
        result = parse_detail_line("SUELDO BASE                1000        1,000,000")
        assert result == ("SUELDO BASE", Decimal("1000000"))

    def test_handles_slash_prefixed_code(self) -> None:
        """Test handles slash prefixed code."""
        result = parse_detail_line("IMPUESTO UNICO              /370          50,000")
        assert result == ("IMPUESTO UNICO", Decimal("50000"))

    def test_keeps_trailing_word_when_it_has_no_digit(self) -> None:
        """A trailing all-letters word is not mistaken for a concept code."""
        result = parse_detail_line("SIMPLE LABEL          1,000")
        assert result == ("SIMPLE LABEL", Decimal("1000"))

    def test_returns_none_when_no_amount_present(self) -> None:
        """Test returns none when no amount present."""
        assert parse_detail_line("                REMANENTES") is None


class TestFindAmountStartColumn:
    """Tests for find_amount_start_column."""

    def test_returns_column_index_of_trailing_amount(self) -> None:
        """Test returns column index of trailing amount."""
        line = "AB 1,000"
        assert find_amount_start_column(line) == line.index("1,000")

    def test_returns_none_when_no_amount(self) -> None:
        """Test returns none when no amount."""
        assert find_amount_start_column("no amount here") is None


class TestFindIncomeDiscountColumnBoundary:
    """Tests for find_income_discount_column_boundary."""

    def test_finds_last_descuentos_occurrence_on_header_row(self) -> None:
        """The section title itself repeats HABERES/DESCUENTOS -- use the last one."""
        boundary = find_income_discount_column_boundary(SAMPLE_TEXT)
        header_line = next(
            line
            for line in SAMPLE_TEXT.splitlines()
            if "HABERES" in line and "DESCUENTOS" in line
        )
        assert boundary == header_line.rindex("DESCUENTOS")

    def test_returns_none_when_header_row_missing(self) -> None:
        """Test returns none when header row missing."""
        assert find_income_discount_column_boundary("no such header") is None
