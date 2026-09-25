"""Generic (employer-agnostic) parsing helpers for Chilean payroll PDFs.

Everything here operates on plain text already extracted from a PDF page --
nothing employer-specific lives in this module (that belongs in template JSON
files, see `templates.py`). This keeps the "how do I read a PDF and find the
header/detail rows" concern fully decoupled from "what does label X mean for
employer Y".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from payroll.infrastructure.logging.logger import logger

_SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
_MONTH_YEAR_RE = re.compile(r"(?i)\b(" + "|".join(_SPANISH_MONTHS) + r")\s+(\d{4})\b")
_ANTIQUITY_AND_WORKED_DAYS_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}\s+(\d{1,2})\b")
_NET_PAY_RE = re.compile(r"(?i)L[IÍ]QUIDO\s+A\s+PAGAR\s+([\d][\d.,]*\d)")
_AMOUNT_AT_END_RE = re.compile(r"(\d{1,3}(?:[.,]\d{3})+)\s*$")
_TRAILING_TOKEN_RE = re.compile(r"\s+([0-9A-Z/]{3,6})\s*$")
_DETAIL_SECTION_START_RE = re.compile(r"(?i)DETALLE")
_DETAIL_SECTION_END_RE = re.compile(r"(?i)TOTALES")
_HABERES_DESCUENTOS_HEADER_RE = re.compile(r"(?i)HABERES.*DESCUENTOS")


def extract_raw_text(content: bytes) -> str | None:
    """Extract raw text from PDF bytes using layout-preserving mode.

    Returns None (never raises) when the PDF cannot be parsed at all -- a
    corrupt upload, an image-only scan with no text layer, or an empty file.
    Layout mode is used (instead of the default flow mode) because it keeps
    columns roughly aligned, which downstream header/detail parsing relies on.
    """
    try:
        reader = PdfReader(BytesIO(content))
        texts = [
            page.extract_text(extraction_mode="layout") or "" for page in reader.pages
        ]
    except (PdfReadError, ValueError, KeyError) as exc:
        logger.warning("pdf_import.text_extraction_failed", error=str(exc))
        return None
    text = "\n".join(texts)
    return text if text.strip() else None


def parse_amount(raw_amount: str) -> Decimal | None:
    """Parse a thousands-grouped amount string (comma or dot separators)."""
    digits_only = re.sub(r"[.,]", "", raw_amount)
    try:
        return Decimal(digits_only)
    except InvalidOperation:
        return None


def parse_period(text: str) -> tuple[int, int] | None:
    """Parse (year, month) from a Spanish month name + 4-digit year pair."""
    match = _MONTH_YEAR_RE.search(text)
    if match is None:
        return None
    month = _SPANISH_MONTHS[match.group(1).lower()]
    year = int(match.group(2))
    return year, month


def parse_worked_days(text: str) -> int | None:
    """Parse worked days from the "antiguedad laboral date + days" pattern."""
    match = _ANTIQUITY_AND_WORKED_DAYS_RE.search(text)
    if match is None:
        return None
    return int(match.group(1))


def parse_declared_net_pay(text: str) -> Decimal | None:
    """Parse the declared net pay ("liquido a pagar") amount."""
    match = _NET_PAY_RE.search(text)
    if match is None:
        return None
    return parse_amount(match.group(1))


@dataclass(frozen=True, slots=True)
class ParsedHeaderFields:
    """Represent header fields parsed from a payroll PDF, best-effort."""

    period_year: int | None
    period_month: int | None
    worked_days: int | None
    declared_net_pay_clp: Decimal | None


def parse_header(text: str) -> ParsedHeaderFields:
    """Parse all employer-agnostic header fields from the extracted text."""
    period = parse_period(text)
    return ParsedHeaderFields(
        period_year=period[0] if period else None,
        period_month=period[1] if period else None,
        worked_days=parse_worked_days(text),
        declared_net_pay_clp=parse_declared_net_pay(text),
    )


def find_detail_lines(text: str) -> list[str]:
    """Return the raw detail lines between the "DETALLE" and "TOTALES" markers.

    Returns an empty list (never raises) when either marker is missing --
    that is treated as "this document's layout could not be located", not as
    an error.
    """
    lines = text.splitlines()
    start_index = next(
        (i for i, line in enumerate(lines) if _DETAIL_SECTION_START_RE.search(line)),
        None,
    )
    if start_index is None:
        return []
    end_index = next(
        (
            i
            for i in range(start_index + 1, len(lines))
            if _DETAIL_SECTION_END_RE.search(lines[i])
        ),
        None,
    )
    if end_index is None:
        return []
    return [line for line in lines[start_index + 1 : end_index] if line.strip()]


def parse_detail_line(line: str) -> tuple[str, Decimal] | None:
    """Split a detail line into (label, amount), stripping a trailing code.

    Returns None when the line has no trailing thousands-grouped amount at
    all (e.g. a blank separator row inside the detail section). A short
    alphanumeric token right before the amount is only treated as a concept
    code (and stripped from the label) when it contains a digit -- every
    real code observed mixes letters and digits ("1E89", "/370", "3C30"),
    which avoids mistaking the last, all-letters word of a label (e.g. a
    label with no code column at all) for one.
    """
    stripped = line.rstrip()
    amount_match = _AMOUNT_AT_END_RE.search(stripped)
    if amount_match is None:
        return None
    amount = parse_amount(amount_match.group(1))
    if amount is None:  # pragma: no cover -- _AMOUNT_AT_END_RE guarantees digits
        return None
    before_amount = stripped[: amount_match.start()]
    token_match = _TRAILING_TOKEN_RE.search(before_amount)
    if token_match is not None and any(char.isdigit() for char in token_match.group(1)):
        label = before_amount[: token_match.start()]
    else:
        label = before_amount
    return label.strip(), amount


def find_amount_start_column(line: str) -> int | None:
    """Return the character column where the trailing amount token starts."""
    match = _AMOUNT_AT_END_RE.search(line.rstrip())
    return None if match is None else match.start()


def find_income_discount_column_boundary(text: str) -> int | None:
    """Locate the character column where the "DESCUENTOS" column starts.

    Used only as a fallback signal to guess `kind` for a detail line that no
    template field matched -- never authoritative when a template did match.
    """
    for line in text.splitlines():
        if _HABERES_DESCUENTOS_HEADER_RE.search(line):
            return line.rindex("DESCUENTOS")
    return None
